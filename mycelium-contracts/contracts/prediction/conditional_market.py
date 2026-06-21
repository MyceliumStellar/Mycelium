"""
Conditional Market — Combinatorial condition paths, joint probability pricing, outcome evaluations.

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
    EVENT_A_NOT_RESOLVED = 11


class OutcomeType:
    A_NO = 0
    A_YES_B_YES = 1
    A_YES_B_NO = 2


@contract
class ConditionalMarket:
    """A conditional prediction market combining paths: If A resolves YES, then B decides winner; else A_NO wins."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        collateral_token: Address,
        oracle_a: Address,
        oracle_b: Address,
        fee_bps: U64,
    ):
        """Initialize the conditional market.

        Args:
            admin: Admin address.
            collateral_token: Backing token.
            oracle_a: Oracle for Event A.
            oracle_b: Oracle for Event B.
            fee_bps: Fee in bps.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("token", collateral_token)
        self.storage.set("oracle_a", oracle_a)
        self.storage.set("oracle_b", oracle_b)
        self.storage.set("fee_bps", fee_bps)

        # Reserves
        self.storage.set("reserve_a_no", U128(0))
        self.storage.set("reserve_y_y", U128(0))
        self.storage.set("reserve_y_n", U128(0))
        self.storage.set("total_lp_shares", U128(0))

        # Resolution States
        # Outcome A: 0 = Unresolved, 1 = YES, 2 = NO
        # Outcome B: 0 = Unresolved, 1 = YES, 2 = NO
        self.storage.set("resolved_a", False)
        self.storage.set("outcome_a", U64(0))
        self.storage.set("resolved_b", False)
        self.storage.set("outcome_b", U64(0))

        self.storage.set("collected_fees", U128(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "token": collateral_token,
            "oracle_a": oracle_a,
            "oracle_b": oracle_b,
        })

    @external
    def add_liquidity(self, lp: Address, amount: U128) -> U128:
        """Add liquidity to the AMM pool by locking collateral and minting A_NO, A_YES_B_YES, A_YES_B_NO."""
        self._require_initialized()
        self._require_not_resolved()
        lp.require_auth()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        token = self.storage.get("token")
        success = self.env.invoke_contract(token, "transfer", [lp, self.env.current_contract_address(), amount])
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        reserve_a_no = self.storage.get("reserve_a_no")
        reserve_y_y = self.storage.get("reserve_y_y")
        reserve_y_n = self.storage.get("reserve_y_n")
        total_lp = self.storage.get("total_lp_shares")

        shares_to_mint = U128(0)
        reserve_sum = reserve_a_no + reserve_y_y + reserve_y_n

        if total_lp == U128(0):
            shares_to_mint = amount
        else:
            shares_to_mint = (amount * total_lp) / reserve_sum

        self.storage.set("reserve_a_no", reserve_a_no + amount)
        self.storage.set("reserve_y_y", reserve_y_y + amount)
        self.storage.set("reserve_y_n", reserve_y_n + amount)
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
    def buy_shares(self, buyer: Address, outcome: U64, collateral_amount: U128, min_shares: U128) -> U128:
        """Buy shares of a conditional outcome.

        Args:
            buyer: Buyer.
            outcome: 0 = A_NO, 1 = A_YES_B_YES, 2 = A_YES_B_NO.
            collateral_amount: Collateral spent.
            min_shares: Slippage check.
        """
        self._require_initialized()
        self._require_not_resolved()
        buyer.require_auth()

        if outcome > U64(2):
            raise ContractError.INVALID_OUTCOME
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

        dx = net_collateral
        r_a_no = self.storage.get("reserve_a_no")
        r_y_y = self.storage.get("reserve_y_y")
        r_y_n = self.storage.get("reserve_y_n")

        # 3-token constant product swap: R_0 * R_1 * R_2 = K
        # If target is A_NO, other reserves increase by dx.
        # R_a_no_new = (R_a_no * R_y_y * R_y_n) / ((R_y_y + dx) * (R_y_n + dx))
        # dy = R_a_no - R_a_no_new
        dy = U128(0)
        if outcome == OutcomeType.A_NO:
            if r_y_y == U128(0) or r_y_n == U128(0):
                raise ContractError.INSUFFICIENT_LIQUIDITY
            denominator = (r_y_y + dx) * (r_y_n + dx)
            r_new = (r_a_no * r_y_y * r_y_n) / denominator
            dy = r_a_no - r_new
            self.storage.set("reserve_a_no", r_new)
            self.storage.set("reserve_y_y", r_y_y + dx)
            self.storage.set("reserve_y_n", r_y_n + dx)
        elif outcome == OutcomeType.A_YES_B_YES:
            if r_a_no == U128(0) or r_y_n == U128(0):
                raise ContractError.INSUFFICIENT_LIQUIDITY
            denominator = (r_a_no + dx) * (r_y_n + dx)
            r_new = (r_a_no * r_y_y * r_y_n) / denominator
            dy = r_y_y - r_new
            self.storage.set("reserve_y_y", r_new)
            self.storage.set("reserve_a_no", r_a_no + dx)
            self.storage.set("reserve_y_n", r_y_n + dx)
        else: # A_YES_B_NO
            if r_a_no == U128(0) or r_y_y == U128(0):
                raise ContractError.INSUFFICIENT_LIQUIDITY
            denominator = (r_a_no + dx) * (r_y_y + dx)
            r_new = (r_a_no * r_y_y * r_y_n) / denominator
            dy = r_y_n - r_new
            self.storage.set("reserve_y_n", r_new)
            self.storage.set("reserve_a_no", r_a_no + dx)
            self.storage.set("reserve_y_y", r_y_y + dx)

        total_received = net_collateral + dy
        if total_received < min_shares:
            raise ContractError.SLIPPAGE_EXCEEDED

        self._add_share_balance(buyer, outcome, total_received)

        self.env.emit_event("shares_bought", {
            "buyer": buyer,
            "outcome": outcome,
            "collateral_spent": collateral_amount,
            "shares_received": total_received,
        })

        return total_received

    @external
    def resolve_event_a(self, caller: Address, outcome: U64):
        """Resolve Event A.

        Args:
            caller: Oracle A.
            outcome: 1 = YES, 2 = NO.
        """
        self._require_initialized()
        caller.require_auth()

        oracle = self.storage.get("oracle_a")
        if caller != oracle:
            raise ContractError.UNAUTHORIZED

        if outcome != U64(1) and outcome != U64(2):
            raise ContractError.INVALID_OUTCOME

        self.storage.set("outcome_a", outcome)
        self.storage.set("resolved_a", True)

        self.env.emit_event("event_a_resolved", {
            "outcome": outcome,
        })

    @external
    def resolve_event_b(self, caller: Address, outcome: U64):
        """Resolve Event B. Only matters if Event A resolves YES.

        Args:
            caller: Oracle B.
            outcome: 1 = YES, 2 = NO.
        """
        self._require_initialized()
        caller.require_auth()

        oracle = self.storage.get("oracle_b")
        if caller != oracle:
            raise ContractError.UNAUTHORIZED

        if outcome != U64(1) and outcome != U64(2):
            raise ContractError.INVALID_OUTCOME

        self.storage.set("outcome_b", outcome)
        self.storage.set("resolved_b", True)

        self.env.emit_event("event_b_resolved", {
            "outcome": outcome,
        })

    @external
    def claim_winnings(self, claimant: Address) -> U128:
        """Claim winnings based on the joint conditional resolutions.

        If A = NO -> A_NO shares win.
        If A = YES and B = YES -> A_YES_B_YES wins.
        If A = YES and B = NO -> A_YES_B_NO wins.
        """
        self._require_initialized()
        claimant.require_auth()

        resolved_a = self.storage.get("resolved_a")
        if not resolved_a:
            raise ContractError.EVENT_A_NOT_RESOLVED

        outcome_a = self.storage.get("outcome_a")
        payout = U128(0)

        if outcome_a == U64(2):  # A is NO
            payout = self._get_share_balance(claimant, OutcomeType.A_NO)
        elif outcome_a == U64(1):  # A is YES, now check B
            resolved_b = self.storage.get("resolved_b")
            if not resolved_b:
                raise ContractError.MARKET_NOT_RESOLVED
            outcome_b = self.storage.get("outcome_b")
            if outcome_b == U64(1):  # B is YES
                payout = self._get_share_balance(claimant, OutcomeType.A_YES_B_YES)
            else:  # B is NO
                payout = self._get_share_balance(claimant, OutcomeType.A_YES_B_NO)

        if payout == U128(0):
            raise ContractError.ZERO_AMOUNT

        # Reset all balances
        self.storage.set(("shares", claimant, OutcomeType.A_NO), U128(0))
        self.storage.set(("shares", claimant, OutcomeType.A_YES_B_YES), U128(0))
        self.storage.set(("shares", claimant, OutcomeType.A_YES_B_NO), U128(0))

        token = self.storage.get("token")
        self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), claimant, payout])

        self.env.emit_event("winnings_claimed", {
            "claimant": claimant,
            "payout": payout,
        })

        return payout

    @external
    def withdraw_fees(self, caller: Address) -> U128:
        """Withdraw collected fees. Only admin."""
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

        return fees

    @view
    def get_market_status(self) -> Map:
        """Get status of condition paths."""
        res = Map()
        res.set("resolved_a", self.storage.get("resolved_a"))
        res.set("outcome_a", self.storage.get("outcome_a"))
        res.set("resolved_b", self.storage.get("resolved_b"))
        res.set("outcome_b", self.storage.get("outcome_b"))
        return res

    @view
    def get_user_balances(self, user: Address) -> Map:
        """Get user conditional share balances."""
        res = Map()
        res.set("a_no", self._get_share_balance(user, OutcomeType.A_NO))
        res.set("a_yes_b_yes", self._get_share_balance(user, OutcomeType.A_YES_B_YES))
        res.set("a_yes_b_no", self._get_share_balance(user, OutcomeType.A_YES_B_NO))
        res.set("lp_shares", self.storage.get(("lp_balance", user), U128(0)))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_not_resolved(self):
        # Resolved is true if final payout is ready
        resolved_a = self.storage.get("resolved_a")
        outcome_a = self.storage.get("outcome_a")
        if resolved_a:
            if outcome_a == U64(2):  # A is NO, resolves everything
                raise ContractError.MARKET_RESOLVED
            if self.storage.get("resolved_b"):
                raise ContractError.MARKET_RESOLVED

    def _get_share_balance(self, user: Address, outcome: U64) -> U128:
        return self.storage.get(("shares", user, outcome), U128(0))

    def _add_share_balance(self, user: Address, outcome: U64, amount: U128):
        current = self._get_share_balance(user, outcome)
        self.storage.set(("shares", user, outcome), current + amount)
