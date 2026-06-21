"""
Binary Prediction — Yes/No prediction pools, AMM-based pricing, fee allocations, and oracle outcome settlements.

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
    INVALID_OUTCOME = 6
    INSUFFICIENT_LIQUIDITY = 7
    INSUFFICIENT_BALANCE = 8
    SLIPPAGE_EXCEEDED = 9
    ZERO_AMOUNT = 10
    MATH_OVERFLOW = 11


@contract
class BinaryPrediction:
    """A prediction market contract for YES/NO outcomes with integrated Constant Product AMM."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        collateral_token: Address,
        oracle: Address,
        fee_bps: U64,
    ):
        """Initialize the prediction market contract.

        Args:
            admin: Admin address who manages parameters.
            collateral_token: Token used to buy/sell shares.
            oracle: Authorized reporter for outcome resolution.
            fee_bps: Trading fee in basis points (100 = 1%).
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("token", collateral_token)
        self.storage.set("oracle", oracle)
        self.storage.set("fee_bps", fee_bps)

        # Pool reserves
        self.storage.set("reserve_yes", U128(0))
        self.storage.set("reserve_no", U128(0))
        self.storage.set("total_lp_shares", U128(0))

        # Outcome: 0 = Unresolved, 1 = YES wins, 2 = NO wins, 3 = Void/Draw
        self.storage.set("outcome", U64(0))
        self.storage.set("resolved", False)
        self.storage.set("collected_fees", U128(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "token": collateral_token,
            "oracle": oracle,
            "fee_bps": fee_bps,
        })

    @external
    def add_liquidity(self, lp: Address, amount: U128) -> U128:
        """Add liquidity to the AMM pool by providing collateral.
        This mints equal amounts of YES and NO shares and deposits them into the pool.

        Args:
            lp: Address of the liquidity provider.
            amount: Amount of collateral token to lock as liquidity.
        """
        self._require_initialized()
        self._require_not_resolved()
        lp.require_auth()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        token = self.storage.get("token")
        
        # Transfer collateral to contract
        success = self.env.invoke_contract(token, "transfer", [lp, self.env.current_contract_address(), amount])
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        reserve_yes = self.storage.get("reserve_yes")
        reserve_no = self.storage.get("reserve_no")
        total_lp = self.storage.get("total_lp_shares")

        shares_to_mint = U128(0)
        if total_lp == U128(0):
            shares_to_mint = amount
        else:
            # Mint proportionally to the minimum reserve share growth
            # (amount * total_lp) / reserve
            shares_to_mint = (amount * total_lp) / (reserve_yes + reserve_no)

        new_reserve_yes = reserve_yes + amount
        new_reserve_no = reserve_no + amount
        new_total_lp = total_lp + shares_to_mint

        self.storage.set("reserve_yes", new_reserve_yes)
        self.storage.set("reserve_no", new_reserve_no)
        self.storage.set("total_lp_shares", new_total_lp)

        # Update LP balance
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
        """Remove liquidity from the pool and receive backing collateral.

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

        reserve_yes = self.storage.get("reserve_yes")
        reserve_no = self.storage.get("reserve_no")

        # Calculate proportional shares to withdraw
        yes_to_withdraw = (reserve_yes * lp_shares) / total_lp
        no_to_withdraw = (reserve_no * lp_shares) / total_lp

        # We merge what we can into collateral, and return the rest as individual shares
        collateral_out = yes_to_withdraw
        if no_to_withdraw < yes_to_withdraw:
            collateral_out = no_to_withdraw

        self.storage.set("reserve_yes", reserve_yes - yes_to_withdraw)
        self.storage.set("reserve_no", reserve_no - no_to_withdraw)
        self.storage.set("total_lp_shares", total_lp - lp_shares)
        self.storage.set(("lp_balance", lp), lp_balance - lp_shares)

        # If YES wins or NO wins already resolved, users redeem differently,
        # but if removing during unresolved state, we merge and give collateral + extra shares.
        token = self.storage.get("token")
        
        # Transfer merged collateral
        if collateral_out > U128(0):
            self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), lp, collateral_out])

        # Credit leftover YES or NO shares to user storage
        if yes_to_withdraw > collateral_out:
            extra_yes = yes_to_withdraw - collateral_out
            self._add_share_balance(lp, Symbol("YES"), extra_yes)
        elif no_to_withdraw > collateral_out:
            extra_no = no_to_withdraw - collateral_out
            self._add_share_balance(lp, Symbol("NO"), extra_no)

        self.env.emit_event("liquidity_removed", {
            "lp": lp,
            "lp_shares": lp_shares,
            "collateral_returned": collateral_out,
        })

        return collateral_out

    @external
    def buy_yes(self, buyer: Address, collateral_amount: U128, min_shares: U128) -> U128:
        """Buy YES shares using collateral.

        Args:
            buyer: Caller address.
            collateral_amount: Collateral token amount to spend.
            min_shares: Minimum YES shares expected (slippage check).
        """
        self._require_initialized()
        self._require_not_resolved()
        buyer.require_auth()

        if collateral_amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        token = self.storage.get("token")
        # Fee deduction
        fee_bps = self.storage.get("fee_bps")
        fee = (collateral_amount * U128(fee_bps)) / U128(10000)
        net_collateral = collateral_amount - fee
        self.storage.set("collected_fees", self.storage.get("collected_fees") + fee)

        success = self.env.invoke_contract(token, "transfer", [buyer, self.env.current_contract_address(), collateral_amount])
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        reserve_yes = self.storage.get("reserve_yes")
        reserve_no = self.storage.get("reserve_no")

        # Under the hood, 1 collateral = 1 YES + 1 NO.
        # We add net_collateral NO shares to the NO reserve, and swap them for YES shares from the YES reserve.
        # dy = reserve_yes * dx / (reserve_no + dx)
        dx = net_collateral
        if reserve_no == U128(0):
            raise ContractError.INSUFFICIENT_LIQUIDITY

        dy = (reserve_yes * dx) / (reserve_no + dx)
        if dy == U128(0):
            raise ContractError.INSUFFICIENT_LIQUIDITY

        # Buyer gets: net_collateral (from minting) + dy (from swap) YES shares
        total_yes_received = net_collateral + dy

        if total_yes_received < min_shares:
            raise ContractError.SLIPPAGE_EXCEEDED

        # Update reserves:
        # reserve_no increases by net_collateral (the minted NO shares are put in the pool)
        # reserve_yes decreases by dy (withdrawn from pool to give to buyer)
        self.storage.set("reserve_no", reserve_no + dx)
        self.storage.set("reserve_yes", reserve_yes - dy)

        self._add_share_balance(buyer, Symbol("YES"), total_yes_received)

        self.env.emit_event("shares_bought", {
            "buyer": buyer,
            "type": Symbol("YES"),
            "collateral_spent": collateral_amount,
            "shares_received": total_yes_received,
        })

        return total_yes_received

    @external
    def buy_no(self, buyer: Address, collateral_amount: U128, min_shares: U128) -> U128:
        """Buy NO shares using collateral.

        Args:
            buyer: Caller address.
            collateral_amount: Collateral token amount to spend.
            min_shares: Minimum NO shares expected.
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

        reserve_yes = self.storage.get("reserve_yes")
        reserve_no = self.storage.get("reserve_no")

        dx = net_collateral
        if reserve_yes == U128(0):
            raise ContractError.INSUFFICIENT_LIQUIDITY

        dy = (reserve_no * dx) / (reserve_yes + dx)
        if dy == U128(0):
            raise ContractError.INSUFFICIENT_LIQUIDITY

        total_no_received = net_collateral + dy

        if total_no_received < min_shares:
            raise ContractError.SLIPPAGE_EXCEEDED

        self.storage.set("reserve_yes", reserve_yes + dx)
        self.storage.set("reserve_no", reserve_no - dy)

        self._add_share_balance(buyer, Symbol("NO"), total_no_received)

        self.env.emit_event("shares_bought", {
            "buyer": buyer,
            "type": Symbol("NO"),
            "collateral_spent": collateral_amount,
            "shares_received": total_no_received,
        })

        return total_no_received

    @external
    def sell_yes(self, seller: Address, yes_amount: U128, min_collateral: U128) -> U128:
        """Sell YES shares back to the AMM pool in exchange for collateral.

        Args:
            seller: Caller address.
            yes_amount: Amount of YES shares to sell.
            min_collateral: Minimum collateral expected.
        """
        self._require_initialized()
        self._require_not_resolved()
        seller.require_auth()

        if yes_amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        yes_balance = self._get_share_balance(seller, Symbol("YES"))
        if yes_balance < yes_amount:
            raise ContractError.INSUFFICIENT_BALANCE

        reserve_yes = self.storage.get("reserve_yes")
        reserve_no = self.storage.get("reserve_no")

        # Swap yes_amount YES shares for NO shares
        # dx = reserve_no * dy / (reserve_yes + dy)
        dy = yes_amount
        dx = (reserve_no * dy) / (reserve_yes + dy)

        # Merge YES and NO shares into collateral
        collateral_out = dx
        if dy < dx:
            collateral_out = dy

        # Update reserves:
        # reserve_yes increases by dy
        # reserve_no decreases by dx
        self.storage.set("reserve_yes", reserve_yes + dy)
        self.storage.set("reserve_no", reserve_no - dx)

        # Deduct user's YES balance
        self._add_share_balance(seller, Symbol("YES"), U128(0) - yes_amount)

        # If there's leftover shares from the swap, credit them to the user
        if dy > collateral_out:
            extra_yes = dy - collateral_out
            self._add_share_balance(seller, Symbol("YES"), extra_yes)
        elif dx > collateral_out:
            extra_no = dx - collateral_out
            self._add_share_balance(seller, Symbol("NO"), extra_no)

        # Apply trading fee on payout
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
            "type": Symbol("YES"),
            "shares_sold": yes_amount,
            "collateral_received": net_collateral,
        })

        return net_collateral

    @external
    def sell_no(self, seller: Address, no_amount: U128, min_collateral: U128) -> U128:
        """Sell NO shares back to the AMM pool in exchange for collateral.

        Args:
            seller: Caller address.
            no_amount: Amount of NO shares to sell.
            min_collateral: Minimum collateral expected.
        """
        self._require_initialized()
        self._require_not_resolved()
        seller.require_auth()

        if no_amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        no_balance = self._get_share_balance(seller, Symbol("NO"))
        if no_balance < no_amount:
            raise ContractError.INSUFFICIENT_BALANCE

        reserve_yes = self.storage.get("reserve_yes")
        reserve_no = self.storage.get("reserve_no")

        dy = no_amount
        dx = (reserve_yes * dy) / (reserve_no + dy)

        collateral_out = dx
        if dy < dx:
            collateral_out = dy

        self.storage.set("reserve_no", reserve_no + dy)
        self.storage.set("reserve_yes", reserve_yes - dx)

        self._add_share_balance(seller, Symbol("NO"), U128(0) - no_amount)

        if dy > collateral_out:
            extra_no = dy - collateral_out
            self._add_share_balance(seller, Symbol("NO"), extra_no)
        elif dx > collateral_out:
            extra_yes = dx - collateral_out
            self._add_share_balance(seller, Symbol("YES"), extra_yes)

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
            "type": Symbol("NO"),
            "shares_sold": no_amount,
            "collateral_received": net_collateral,
        })

        return net_collateral

    @external
    def resolve(self, caller: Address, outcome: U64):
        """Resolve the prediction market outcome. Only oracle.

        Args:
            caller: Authoritative oracle or administrator.
            outcome: Winning outcome (1 = YES wins, 2 = NO wins, 3 = Void/Draw).
        """
        self._require_initialized()
        self._require_not_resolved()
        caller.require_auth()

        oracle = self.storage.get("oracle")
        if caller != oracle:
            raise ContractError.UNAUTHORIZED

        if outcome < U64(1) or outcome > U64(3):
            raise ContractError.INVALID_OUTCOME

        self.storage.set("outcome", outcome)
        self.storage.set("resolved", True)

        self.env.emit_event("market_resolved", {
            "outcome": outcome,
        })

    @external
    def claim_winnings(self, claimant: Address) -> U128:
        """Claim winnings based on the resolved outcome of the prediction market.

        Args:
            claimant: Address of the shareholder.
        """
        self._require_initialized()
        self._require_resolved()
        claimant.require_auth()

        outcome = self.storage.get("outcome")
        payout = U128(0)

        if outcome == U64(1):  # YES wins
            payout = self._get_share_balance(claimant, Symbol("YES"))
            self.storage.set(("shares", claimant, Symbol("YES")), U128(0))
        elif outcome == U64(2):  # NO wins
            payout = self._get_share_balance(claimant, Symbol("NO"))
            self.storage.set(("shares", claimant, Symbol("NO")), U128(0))
        elif outcome == U64(3):  # Void/Draw: YES and NO shares claim 0.5 collateral each
            yes_bal = self._get_share_balance(claimant, Symbol("YES"))
            no_bal = self._get_share_balance(claimant, Symbol("NO"))
            payout = (yes_bal + no_bal) / U128(2)
            self.storage.set(("shares", claimant, Symbol("YES")), U128(0))
            self.storage.set(("shares", claimant, Symbol("NO")), U128(0))

        if payout == U128(0):
            raise ContractError.ZERO_AMOUNT

        token = self.storage.get("token")
        self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), claimant, payout])

        self.env.emit_event("winnings_claimed", {
            "claimant": claimant,
            "payout": payout,
        })

        return payout

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
        """Get the current YES/NO reserves and total LP shares."""
        res = Map()
        res.set("reserve_yes", self.storage.get("reserve_yes"))
        res.set("reserve_no", self.storage.get("reserve_no"))
        res.set("total_lp_shares", self.storage.get("total_lp_shares"))
        return res

    @view
    def get_price(self) -> Map:
        """Get current market price for YES and NO shares (out of 1.0 collateral, scaled to 1e4)."""
        reserve_yes = self.storage.get("reserve_yes")
        reserve_no = self.storage.get("reserve_no")
        total = reserve_yes + reserve_no

        prices = Map()
        if total == U128(0):
            prices.set("yes_price_bps", U64(5000))
            prices.set("no_price_bps", U64(5000))
        else:
            # YES price is proportional to NO pool size: higher NO pool means less YES shares left, hence YES is more expensive
            yes_price = (reserve_no * U128(10000)) / total
            no_price = (reserve_yes * U128(10000)) / total
            prices.set("yes_price_bps", U64(yes_price))
            prices.set("no_price_bps", U64(no_price))
        return prices

    @view
    def get_user_balances(self, user: Address) -> Map:
        """Get current share and LP balances of a user."""
        res = Map()
        res.set("yes_shares", self._get_share_balance(user, Symbol("YES")))
        res.set("no_shares", self._get_share_balance(user, Symbol("NO")))
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
