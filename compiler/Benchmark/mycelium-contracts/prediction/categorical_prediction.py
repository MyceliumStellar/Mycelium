"""
Categorical Prediction — Multiple categories, winner-take-all settlement, dispute window periods.

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
    INVALID_CATEGORY = 6
    DISPUTE_WINDOW_CLOSED = 7
    DISPUTE_WINDOW_ACTIVE = 8
    MARKET_DISPUTED = 9
    INSUFFICIENT_LIQUIDITY = 10
    INSUFFICIENT_BALANCE = 11
    SLIPPAGE_EXCEEDED = 12
    ZERO_AMOUNT = 13


class MarketState:
    UNRESOLVED = 0
    RESOLVED = 1
    DISPUTED = 2
    SETTLED = 3


@contract
class CategoricalPrediction:
    """A prediction market contract for multi-category outcomes (Winner-Take-All) with dispute window."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        collateral_token: Address,
        oracle: Address,
        categories: Vec,
        dispute_duration: U64,
        dispute_bond: U128,
        fee_bps: U64,
    ):
        """Initialize the categorical prediction contract.

        Args:
            admin: Admin address.
            collateral_token: Backing token address.
            oracle: Resolution oracle address.
            categories: Vector of Symbols representing the prediction options.
            dispute_duration: Length of dispute window in seconds.
            dispute_bond: Bond amount required to raise a dispute.
            fee_bps: Trading fee (bps).
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("token", collateral_token)
        self.storage.set("oracle", oracle)
        self.storage.set("dispute_duration", dispute_duration)
        self.storage.set("dispute_bond", dispute_bond)
        self.storage.set("fee_bps", fee_bps)

        # Store categories
        self.storage.set("categories", categories)
        for i in range(len(categories)):
            cat = categories.get(i)
            self.storage.set(("reserve", cat), U128(0))

        self.storage.set("total_lp_shares", U128(0))
        self.storage.set("state", MarketState.UNRESOLVED)
        self.storage.set("winning_category", Symbol(""))
        self.storage.set("dispute_end", U64(0))
        self.storage.set("disputer", Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"))  # Dummy initial address
        self.storage.set("collected_fees", U128(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "token": collateral_token,
            "oracle": oracle,
            "categories_count": len(categories),
        })

    @external
    def add_liquidity(self, lp: Address, amount: U128) -> U128:
        """Add liquidity to the multi-token AMM. Lock collateral to mint equal shares of all categories.

        Args:
            lp: Liquidity provider.
            amount: Collateral amount.
        """
        self._require_initialized()
        self._require_state(MarketState.UNRESOLVED)
        lp.require_auth()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        token = self.storage.get("token")
        success = self.env.invoke_contract(token, "transfer", [lp, self.env.current_contract_address(), amount])
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        categories = self.storage.get("categories")
        num_cats = len(categories)

        total_lp = self.storage.get("total_lp_shares")
        shares_to_mint = U128(0)

        # Calculate reserve sum
        reserve_sum = U128(0)
        for i in range(num_cats):
            cat = categories.get(i)
            reserve_sum = reserve_sum + self.storage.get(("reserve", cat))

        if total_lp == U128(0):
            shares_to_mint = amount
        else:
            shares_to_mint = (amount * total_lp) / reserve_sum

        for i in range(num_cats):
            cat = categories.get(i)
            res = self.storage.get(("reserve", cat))
            self.storage.set(("reserve", cat), res + amount)

        self.storage.set("total_lp_shares", total_lp + shares_to_mint)
        lp_bal = self.storage.get(("lp_balance", lp), U128(0))
        self.storage.set(("lp_balance", lp), lp_bal + shares_to_mint)

        self.env.emit_event("liquidity_added", {
            "lp": lp,
            "amount": amount,
            "shares_minted": shares_to_mint,
        })

        return shares_to_mint

    @external
    def buy_shares(self, buyer: Address, category: Symbol, collateral_amount: U128, min_shares: U128) -> U128:
        """Buy shares of a specific category.

        Args:
            buyer: Buyer address.
            category: Selected option symbol.
            collateral_amount: Collateral spent.
            min_shares: Slippage check.
        """
        self._require_initialized()
        self._require_state(MarketState.UNRESOLVED)
        buyer.require_auth()

        if not self._is_valid_category(category):
            raise ContractError.INVALID_CATEGORY
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

        categories = self.storage.get("categories")
        dx = net_collateral

        # Swap math: dy = R_target * (1 - product_{j != target} (R_j / (R_j + dx)))
        r_target = self.storage.get(("reserve", category))
        if r_target == U128(0):
            raise ContractError.INSUFFICIENT_LIQUIDITY

        # Compute product of R_j / (R_j + dx) scaled by 1e6
        prod = U128(1000000)
        for i in range(len(categories)):
            cat = categories.get(i)
            if cat != category:
                r_j = self.storage.get(("reserve", cat))
                if r_j == U128(0):
                    raise ContractError.INSUFFICIENT_LIQUIDITY
                prod = (prod * r_j) / (r_j + dx)

        dy = r_target - ((r_target * prod) / U128(1000000))
        total_received = net_collateral + dy

        if total_received < min_shares:
            raise ContractError.SLIPPAGE_EXCEEDED

        # Update reserves: target decreases by dy, others increase by dx
        self.storage.set(("reserve", category), r_target - dy)
        for i in range(len(categories)):
            cat = categories.get(i)
            if cat != category:
                self.storage.set(("reserve", cat), self.storage.get(("reserve", cat)) + dx)

        self._add_share_balance(buyer, category, total_received)

        self.env.emit_event("shares_bought", {
            "buyer": buyer,
            "category": category,
            "collateral_spent": collateral_amount,
            "shares_received": total_received,
        })

        return total_received

    @external
    def resolve(self, caller: Address, category: Symbol):
        """Initial resolution by oracle. Starts the dispute window.

        Args:
            caller: Resolution oracle.
            category: Winning category.
        """
        self._require_initialized()
        self._require_state(MarketState.UNRESOLVED)
        caller.require_auth()

        oracle = self.storage.get("oracle")
        if caller != oracle:
            raise ContractError.UNAUTHORIZED

        if not self._is_valid_category(category):
            raise ContractError.INVALID_CATEGORY

        now = self.env.ledger().timestamp()
        dispute_duration = self.storage.get("dispute_duration")

        self.storage.set("winning_category", category)
        self.storage.set("state", MarketState.RESOLVED)
        self.storage.set("dispute_end", now + dispute_duration)

        self.env.emit_event("market_resolved", {
            "proposed_winner": category,
            "dispute_end": now + dispute_duration,
        })

    @external
    def dispute(self, disputer: Address, reason: Symbol):
        """Dispute the proposed outcome. Requires posting a dispute bond.

        Args:
            disputer: Address raising the dispute.
            reason: Symbol indicating the reason.
        """
        self._require_initialized()
        self._require_state(MarketState.RESOLVED)
        disputer.require_auth()

        now = self.env.ledger().timestamp()
        dispute_end = self.storage.get("dispute_end")
        if now > dispute_end:
            raise ContractError.DISPUTE_WINDOW_CLOSED

        bond = self.storage.get("dispute_bond")
        token = self.storage.get("token")

        success = self.env.invoke_contract(token, "transfer", [disputer, self.env.current_contract_address(), bond])
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        self.storage.set("state", MarketState.DISPUTED)
        self.storage.set("disputer", disputer)

        self.env.emit_event("market_disputed", {
            "disputer": disputer,
            "reason": reason,
        })

    @external
    def settle_dispute(self, caller: Address, final_category: Symbol):
        """Settle a disputed market. Only admin.

        Args:
            caller: Admin.
            final_category: Final corrected outcome.
        """
        self._require_initialized()
        self._require_state(MarketState.DISPUTED)
        caller.require_auth()

        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

        if not self._is_valid_category(final_category):
            raise ContractError.INVALID_CATEGORY

        # Handle dispute bond refund or penalty
        proposed_winner = self.storage.get("winning_category")
        disputer = self.storage.get("disputer")
        bond = self.storage.get("dispute_bond")
        token = self.storage.get("token")

        if final_category != proposed_winner:
            # Disputer was correct, refund bond
            self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), disputer, bond])
        else:
            # Disputer was wrong, burn or keep bond in treasury (accumulate to fees)
            fees = self.storage.get("collected_fees")
            self.storage.set("collected_fees", fees + bond)

        self.storage.set("winning_category", final_category)
        self.storage.set("state", MarketState.SETTLED)

        self.env.emit_event("dispute_settled", {
            "final_winner": final_category,
        })

    @external
    def claim_winnings(self, claimant: Address) -> U128:
        """Claim winnings once resolved and dispute window passes, or after dispute is settled.

        Args:
            claimant: Shareholder address.
        """
        self._require_initialized()
        
        state = self.storage.get("state")
        if state == MarketState.RESOLVED:
            # Verify dispute window has expired
            now = self.env.ledger().timestamp()
            dispute_end = self.storage.get("dispute_end")
            if now <= dispute_end:
                raise ContractError.DISPUTE_WINDOW_ACTIVE
            # Transition to settled
            self.storage.set("state", MarketState.SETTLED)
        elif state == MarketState.UNRESOLVED or state == MarketState.DISPUTED:
            raise ContractError.MARKET_NOT_RESOLVED

        claimant.require_auth()

        winner = self.storage.get("winning_category")
        shares = self._get_share_balance(claimant, winner)
        if shares == U128(0):
            raise ContractError.ZERO_AMOUNT

        # Clear balances for all categories to prevent re-entrancy / double claims
        categories = self.storage.get("categories")
        for i in range(len(categories)):
            cat = categories.get(i)
            self.storage.set(("shares", claimant, cat), U128(0))

        token = self.storage.get("token")
        self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), claimant, shares])

        self.env.emit_event("winnings_claimed", {
            "claimant": claimant,
            "payout": shares,
        })

        return shares

    @external
    def withdraw_fees(self, caller: Address) -> U128:
        """Withdraw fees. Only admin.

        Args:
            caller: Admin.
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
    def get_market_status(self) -> Map:
        """Get status of the market."""
        res = Map()
        res.set("state", self.storage.get("state"))
        res.set("winning_category", self.storage.get("winning_category"))
        res.set("dispute_end", self.storage.get("dispute_end"))
        return res

    @view
    def get_user_balances(self, user: Address) -> Map:
        """Get categorical shares of the user."""
        categories = self.storage.get("categories")
        res = Map()
        for i in range(len(categories)):
            cat = categories.get(i)
            res.set(cat, self._get_share_balance(user, cat))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_state(self, expected: U64):
        state = self.storage.get("state")
        if state != expected:
            raise ContractError.MARKET_RESOLVED

    def _is_valid_category(self, category: Symbol) -> Bool:
        categories = self.storage.get("categories")
        for i in range(len(categories)):
            if categories.get(i) == category:
                return True
        return False

    def _get_share_balance(self, user: Address, category: Symbol) -> U128:
        return self.storage.get(("shares", user, category), U128(0))

    def _add_share_balance(self, user: Address, category: Symbol, amount: U128):
        current = self._get_share_balance(user, category)
        self.storage.set(("shares", user, category), current + amount)
