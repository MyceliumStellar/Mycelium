"""
Scalar Prediction — Continuous scalar outcome ranges, bounds verification, proportional payout maps.

Mycelium Smart Contract for Stellar
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    MARKET_RESOLVED = 4
    MARKET_NOT_RESOLVED = 5
    INVALID_BOUNDS = 6
    INVALID_OUTCOME = 7
    INSUFFICIENT_LIQUIDITY = 8
    INSUFFICIENT_BALANCE = 9
    SLIPPAGE_EXCEEDED = 10
    ZERO_AMOUNT = 11


@contract
class ScalarPrediction:
    """A scalar prediction market contract for continuous outcomes between lower and upper bounds."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        collateral_token: Address,
        oracle: Address,
        lower_bound: I128,
        upper_bound: I128,
        fee_bps: U64,
    ):
        """Initialize the scalar prediction market contract.

        Args:
            admin: Admin address.
            collateral_token: Token used for shares trading.
            oracle: Authorized reporter for continuous outcome.
            lower_bound: The lower limit of the scalar range.
            upper_bound: The upper limit of the scalar range.
            fee_bps: Trading fee in basis points (100 = 1%).
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if lower_bound >= upper_bound:
            raise ContractError.INVALID_BOUNDS

        self.storage.set("admin", admin)
        self.storage.set("token", collateral_token)
        self.storage.set("oracle", oracle)
        self.storage.set("lower_bound", lower_bound)
        self.storage.set("upper_bound", upper_bound)
        self.storage.set("fee_bps", fee_bps)

        # Pool reserves for LONG and SHORT
        self.storage.set("reserve_long", U128(0))
        self.storage.set("reserve_short", U128(0))
        self.storage.set("total_lp_shares", U128(0))

        # Outcome: 0 = Unresolved, 1 = Resolved
        self.storage.set("resolved", False)
        # Settled value
        self.storage.set("settled_value", I128(0))
        self.storage.set("collected_fees", U128(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "token": collateral_token,
            "oracle": oracle,
            "lower_bound": lower_bound,
            "upper_bound": upper_bound,
        })

    @external
    def add_liquidity(self, lp: Address, amount: U128) -> U128:
        """Add liquidity to the AMM pool by depositing collateral.
        Mints equal amounts of LONG and SHORT shares.

        Args:
            lp: Address of the liquidity provider.
            amount: Amount of collateral token.
        """
        self._require_initialized()
        self._require_not_resolved()
        lp.require_auth()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        token = self.storage.get("token")
        success = self.env.invoke_contract(token, "transfer", [lp, self.env.current_contract_address(), amount])
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        reserve_long = self.storage.get("reserve_long")
        reserve_short = self.storage.get("reserve_short")
        total_lp = self.storage.get("total_lp_shares")

        shares_to_mint = U128(0)
        if total_lp == U128(0):
            shares_to_mint = amount
        else:
            shares_to_mint = (amount * total_lp) / (reserve_long + reserve_short)

        self.storage.set("reserve_long", reserve_long + amount)
        self.storage.set("reserve_short", reserve_short + amount)
        self.storage.set("total_lp_shares", total_lp + shares_to_mint)

        lp_balance = self.storage.get(("lp_balance", lp), U128(0))
        self.storage.set(("lp_balance", lp), lp_balance + shares_to_mint)

        self.env.emit_event("liquidity_added", {
            "lp": lp,
            "amount": amount,
            "shares_minted": shares_to_mint,
        })

        return shares_to_mint

    @external
    def remove_liquidity(self, lp: Address, lp_shares: U128) -> U128:
        """Remove liquidity from the pool and receive backing shares/collateral.

        Args:
            lp: Address of the liquidity provider.
            lp_shares: LP token shares to burn.
        """
        self._require_initialized()
        lp.require_auth()

        if lp_shares == U128(0):
            raise ContractError.ZERO_AMOUNT

        total_lp = self.storage.get("total_lp_shares")
        lp_balance = self.storage.get(("lp_balance", lp), U128(0))

        if lp_balance < lp_shares:
            raise ContractError.INSUFFICIENT_BALANCE

        reserve_long = self.storage.get("reserve_long")
        reserve_short = self.storage.get("reserve_short")

        long_to_withdraw = (reserve_long * lp_shares) / total_lp
        short_to_withdraw = (reserve_short * lp_shares) / total_lp

        # Merge what we can into collateral
        collateral_out = long_to_withdraw
        if short_to_withdraw < long_to_withdraw:
            collateral_out = short_to_withdraw

        self.storage.set("reserve_long", reserve_long - long_to_withdraw)
        self.storage.set("reserve_short", reserve_short - short_to_withdraw)
        self.storage.set("total_lp_shares", total_lp - lp_shares)
        self.storage.set(("lp_balance", lp), lp_balance - lp_shares)

        token = self.storage.get("token")
        if collateral_out > U128(0):
            self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), lp, collateral_out])

        if long_to_withdraw > collateral_out:
            extra_long = long_to_withdraw - collateral_out
            self._add_share_balance(lp, Symbol("LONG"), extra_long)
        elif short_to_withdraw > collateral_out:
            extra_short = short_to_withdraw - collateral_out
            self._add_share_balance(lp, Symbol("SHORT"), extra_short)

        self.env.emit_event("liquidity_removed", {
            "lp": lp,
            "lp_shares": lp_shares,
            "collateral_returned": collateral_out,
        })

        return collateral_out

    @external
    def buy_long(self, buyer: Address, collateral_amount: U128, min_shares: U128) -> U128:
        """Buy LONG shares using collateral.

        Args:
            buyer: Caller address.
            collateral_amount: Collateral token amount.
            min_shares: Minimum LONG shares expected.
        """
        self._require_initialized()
        self._require_not_resolved()
        buyer.require_auth()

        if collateral_amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        token = self.storage.get("token")
        fee_bps = self.storage.get("fee_bps")
        fee = (collateral_amount * U128(fee_bps)) / U128(10000)
        net_collateral = collateral_amount - fee
        self.storage.set("collected_fees", self.storage.get("collected_fees") + fee)

        success = self.env.invoke_contract(token, "transfer", [buyer, self.env.current_contract_address(), collateral_amount])
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        reserve_long = self.storage.get("reserve_long")
        reserve_short = self.storage.get("reserve_short")

        dx = net_collateral
        if reserve_short == U128(0):
            raise ContractError.INSUFFICIENT_LIQUIDITY

        dy = (reserve_long * dx) / (reserve_short + dx)
        if dy == U128(0):
            raise ContractError.INSUFFICIENT_LIQUIDITY

        total_long_received = net_collateral + dy
        if total_long_received < min_shares:
            raise ContractError.SLIPPAGE_EXCEEDED

        self.storage.set("reserve_short", reserve_short + dx)
        self.storage.set("reserve_long", reserve_long - dy)

        self._add_share_balance(buyer, Symbol("LONG"), total_long_received)

        self.env.emit_event("shares_bought", {
            "buyer": buyer,
            "type": Symbol("LONG"),
            "collateral_spent": collateral_amount,
            "shares_received": total_long_received,
        })

        return total_long_received

    @external
    def buy_short(self, buyer: Address, collateral_amount: U128, min_shares: U128) -> U128:
        """Buy SHORT shares using collateral.

        Args:
            buyer: Caller address.
            collateral_amount: Collateral token amount.
            min_shares: Minimum SHORT shares expected.
        """
        self._require_initialized()
        self._require_not_resolved()
        buyer.require_auth()

        if collateral_amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        token = self.storage.get("token")
        fee_bps = self.storage.get("fee_bps")
        fee = (collateral_amount * U128(fee_bps)) / U128(10000)
        net_collateral = collateral_amount - fee
        self.storage.set("collected_fees", self.storage.get("collected_fees") + fee)

        success = self.env.invoke_contract(token, "transfer", [buyer, self.env.current_contract_address(), collateral_amount])
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        reserve_long = self.storage.get("reserve_long")
        reserve_short = self.storage.get("reserve_short")

        dx = net_collateral
        if reserve_long == U128(0):
            raise ContractError.INSUFFICIENT_LIQUIDITY

        dy = (reserve_short * dx) / (reserve_long + dx)
        if dy == U128(0):
            raise ContractError.INSUFFICIENT_LIQUIDITY

        total_short_received = net_collateral + dy
        if total_short_received < min_shares:
            raise ContractError.SLIPPAGE_EXCEEDED

        self.storage.set("reserve_long", reserve_long + dx)
        self.storage.set("reserve_short", reserve_short - dy)

        self._add_share_balance(buyer, Symbol("SHORT"), total_short_received)

        self.env.emit_event("shares_bought", {
            "buyer": buyer,
            "type": Symbol("SHORT"),
            "collateral_spent": collateral_amount,
            "shares_received": total_short_received,
        })

        return total_short_received

    @external
    def sell_long(self, seller: Address, long_amount: U128, min_collateral: U128) -> U128:
        """Sell LONG shares back to the AMM pool.

        Args:
            seller: Caller address.
            long_amount: LONG shares to sell.
            min_collateral: Minimum collateral expected.
        """
        self._require_initialized()
        self._require_not_resolved()
        seller.require_auth()

        if long_amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        long_balance = self._get_share_balance(seller, Symbol("LONG"))
        if long_balance < long_amount:
            raise ContractError.INSUFFICIENT_BALANCE

        reserve_long = self.storage.get("reserve_long")
        reserve_short = self.storage.get("reserve_short")

        dy = long_amount
        dx = (reserve_short * dy) / (reserve_long + dy)

        collateral_out = dx
        if dy < dx:
            collateral_out = dy

        self.storage.set("reserve_long", reserve_long + dy)
        self.storage.set("reserve_short", reserve_short - dx)

        self._add_share_balance(seller, Symbol("LONG"), U128(0) - long_amount)

        if dy > collateral_out:
            extra_long = dy - collateral_out
            self._add_share_balance(seller, Symbol("LONG"), extra_long)
        elif dx > collateral_out:
            extra_short = dx - collateral_out
            self._add_share_balance(seller, Symbol("SHORT"), extra_short)

        fee_bps = self.storage.get("fee_bps")
        fee = (collateral_out * U128(fee_bps)) / U128(10000)
        net_collateral = collateral_out - fee
        self.storage.set("collected_fees", self.storage.get("collected_fees") + fee)

        if net_collateral < min_collateral:
            raise ContractError.SLIPPAGE_EXCEEDED

        token = self.storage.get("token")
        self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), seller, net_collateral])

        self.env.emit_event("shares_sold", {
            "seller": seller,
            "type": Symbol("LONG"),
            "shares_sold": long_amount,
            "collateral_received": net_collateral,
        })

        return net_collateral

    @external
    def sell_short(self, seller: Address, short_amount: U128, min_collateral: U128) -> U128:
        """Sell SHORT shares back to the AMM pool.

        Args:
            seller: Caller address.
            short_amount: SHORT shares to sell.
            min_collateral: Minimum collateral expected.
        """
        self._require_initialized()
        self._require_not_resolved()
        seller.require_auth()

        if short_amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        short_balance = self._get_share_balance(seller, Symbol("SHORT"))
        if short_balance < short_amount:
            raise ContractError.INSUFFICIENT_BALANCE

        reserve_long = self.storage.get("reserve_long")
        reserve_short = self.storage.get("reserve_short")

        dy = short_amount
        dx = (reserve_long * dy) / (reserve_short + dy)

        collateral_out = dx
        if dy < dx:
            collateral_out = dy

        self.storage.set("reserve_short", reserve_short + dy)
        self.storage.set("reserve_long", reserve_long - dx)

        self._add_share_balance(seller, Symbol("SHORT"), U128(0) - short_amount)

        if dy > collateral_out:
            extra_short = dy - collateral_out
            self._add_share_balance(seller, Symbol("SHORT"), extra_short)
        elif dx > collateral_out:
            extra_long = dx - collateral_out
            self._add_share_balance(seller, Symbol("LONG"), extra_long)

        fee_bps = self.storage.get("fee_bps")
        fee = (collateral_out * U128(fee_bps)) / U128(10000)
        net_collateral = collateral_out - fee
        self.storage.set("collected_fees", self.storage.get("collected_fees") + fee)

        if net_collateral < min_collateral:
            raise ContractError.SLIPPAGE_EXCEEDED

        token = self.storage.get("token")
        self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), seller, net_collateral])

        self.env.emit_event("shares_sold", {
            "seller": seller,
            "type": Symbol("SHORT"),
            "shares_sold": short_amount,
            "collateral_received": net_collateral,
        })

        return net_collateral

    @external
    def resolve(self, caller: Address, settled_value: I128):
        """Resolve the scalar prediction market outcome. Only oracle.

        Args:
            caller: Authoritative oracle.
            settled_value: The verified final continuous value.
        """
        self._require_initialized()
        self._require_not_resolved()
        caller.require_auth()

        oracle = self.storage.get("oracle")
        if caller != oracle:
            raise ContractError.UNAUTHORIZED

        self.storage.set("settled_value", settled_value)
        self.storage.set("resolved", True)

        self.env.emit_event("market_resolved", {
            "settled_value": settled_value,
        })

    @external
    def claim_winnings(self, claimant: Address) -> U128:
        """Claim winnings based on proportional scalar outcome calculation.

        LONG shares payout = (V - lower) / (upper - lower) collateral.
        SHORT shares payout = (upper - V) / (upper - lower) collateral.

        Args:
            claimant: Address of the shareholder.
        """
        self._require_initialized()
        self._require_resolved()
        claimant.require_auth()

        lower = self.storage.get("lower_bound")
        upper = self.storage.get("upper_bound")
        v = self.storage.get("settled_value")

        long_bal = self._get_share_balance(claimant, Symbol("LONG"))
        short_bal = self._get_share_balance(claimant, Symbol("SHORT"))

        if long_bal == U128(0) and short_bal == U128(0):
            raise ContractError.ZERO_AMOUNT

        # Cap value between bounds for safety
        val = v
        if val < lower:
            val = lower
        elif val > upper:
            val = upper

        range_len = upper - lower
        # Compute LONG and SHORT weights scaled to 1e6
        long_weight = ((val - lower) * I128(1000000)) / range_len
        short_weight = I128(1000000) - long_weight

        # Calculate proportional payouts
        payout_long = (long_bal * U128(long_weight)) / U128(1000000)
        payout_short = (short_bal * U128(short_weight)) / U128(1000000)
        total_payout = payout_long + payout_short

        if total_payout == U128(0):
            raise ContractError.ZERO_AMOUNT

        self.storage.set(("shares", claimant, Symbol("LONG")), U128(0))
        self.storage.set(("shares", claimant, Symbol("SHORT")), U128(0))

        token = self.storage.get("token")
        self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), claimant, total_payout])

        self.env.emit_event("winnings_claimed", {
            "claimant": claimant,
            "payout": total_payout,
        })

        return total_payout

    @external
    def withdraw_fees(self, caller: Address) -> U128:
        """Withdraw collected trading fees. Only admin.

        Args:
            caller: Admin address.
        """
        self._require_initialized()
        caller.require_auth()

        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

        fees = self.storage.get("collected_fees")
        if fees == U128(0):
            raise ContractError.ZERO_AMOUNT

        self.storage.set("collected_fees", U128(0))
        token = self.storage.get("token")
        self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), admin, fees])

        self.env.emit_event("fees_withdrawn", {
            "admin": admin,
            "amount": fees,
        })

        return fees

    @view
    def get_pool_reserves(self) -> Map:
        """Get current reserves."""
        res = Map()
        res.set("reserve_long", self.storage.get("reserve_long"))
        res.set("reserve_short", self.storage.get("reserve_short"))
        res.set("total_lp_shares", self.storage.get("total_lp_shares"))
        return res

    @view
    def get_price(self) -> Map:
        """Get LONG / SHORT prices out of 1.0 (scaled to 1e4)."""
        reserve_long = self.storage.get("reserve_long")
        reserve_short = self.storage.get("reserve_short")
        total = reserve_long + reserve_short

        prices = Map()
        if total == U128(0):
            prices.set("long_price_bps", U64(5000))
            prices.set("short_price_bps", U64(5000))
        else:
            long_price = (reserve_short * U128(10000)) / total
            short_price = (reserve_long * U128(10000)) / total
            prices.set("long_price_bps", U64(long_price))
            prices.set("short_price_bps", U64(short_price))
        return prices

    @view
    def get_user_balances(self, user: Address) -> Map:
        """Get user balances."""
        res = Map()
        res.set("long_shares", self._get_share_balance(user, Symbol("LONG")))
        res.set("short_shares", self._get_share_balance(user, Symbol("SHORT")))
        res.set("lp_shares", self.storage.get(("lp_balance", user), U128(0)))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_not_resolved(self):
        if self.storage.get("resolved", False):
            raise ContractError.MARKET_RESOLVED

    def _require_resolved(self):
        if not self.storage.get("resolved", False):
            raise ContractError.MARKET_NOT_RESOLVED

    def _get_share_balance(self, user: Address, token_type: Symbol) -> U128:
        return self.storage.get(("shares", user, token_type), U128(0))

    def _add_share_balance(self, user: Address, token_type: Symbol, amount: U128):
        current = self._get_share_balance(user, token_type)
        self.storage.set(("shares", user, token_type), current + amount)
