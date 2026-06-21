"""
Futarchy Market — Policy markets, token balances tracking, impact verification.

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
    DECISION_NOT_REACHED = 4
    DECISION_ALREADY_EVALUATED = 5
    MARKET_NOT_RESOLVED = 6
    INVALID_OUTCOME = 7
    INSUFFICIENT_BALANCE = 8
    SLIPPAGE_EXCEEDED = 9
    ZERO_AMOUNT = 10
    DECISION_PERIOD_ACTIVE = 11
    IMPACT_NOT_RESOLVED = 12


class FutarchyState:
    PROPOSAL_ACTIVE = 0
    DECISION_EVALUATED = 1
    RESOLVED = 2


@contract
class FutarchyMarket:
    """A Futarchy market contract for policy decision markets utilizing conditional prediction pools."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        collateral_token: Address,
        oracle: Address,
        decision_deadline: U64,
        impact_duration: U64,
    ):
        """Initialize the Futarchy market.

        Args:
            admin: Admin address.
            collateral_token: Collateral token address.
            oracle: Evaluation oracle address.
            decision_deadline: Timestamp until which policy trading is allowed before evaluating the decision.
            impact_duration: Delay in seconds after decision evaluation before actual impact is verified.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("token", collateral_token)
        self.storage.set("oracle", oracle)
        self.storage.set("decision_deadline", decision_deadline)
        self.storage.set("impact_duration", impact_duration)

        # CPMM Reserves for policy YES (YES_LONG vs YES_SHORT)
        self.storage.set("res_yes_long", U128(1000 * 1000000)) # Init with virtual liquidity
        self.storage.set("res_yes_short", U128(1000 * 1000000))
        # CPMM Reserves for policy NO (NO_LONG vs NO_SHORT)
        self.storage.set("res_no_long", U128(1000 * 1000000))
        self.storage.set("res_no_short", U128(1000 * 1000000))

        self.storage.set("state", FutarchyState.PROPOSAL_ACTIVE)
        # Decision: 0 = None, 1 = YES policy passed, 2 = NO policy passed
        self.storage.set("decision", U64(0))
        self.storage.set("resolution_time", U64(0))

        # Proportional scalar bounds for final impact resolution
        self.storage.set("lower_bound", I128(0))
        self.storage.set("upper_bound", I128(100)) # E.g., metric range 0 to 100
        self.storage.set("final_metric_value", I128(0))

        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "deadline": decision_deadline,
        })

    @external
    def mint_policy_shares(self, caller: Address, amount: U128):
        """Mint both LONG and SHORT shares for BOTH policies (USDC locked 1:1 for YES and NO sets).

        Args:
            caller: Minter.
            amount: Amount of collateral.
        """
        self._require_initialized()
        self._require_state(FutarchyState.PROPOSAL_ACTIVE)
        caller.require_auth()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        token = self.storage.get("token")
        success = self.env.invoke_contract(token, "transfer", [caller, self.env.current_contract_address(), amount])
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        # Mint both outcomes for both YES and NO paths
        self._add_balance(caller, Symbol("YES_LONG"), amount)
        self._add_balance(caller, Symbol("YES_SHORT"), amount)
        self._add_balance(caller, Symbol("NO_LONG"), amount)
        self._add_balance(caller, Symbol("NO_SHORT"), amount)

        self.env.emit_event("shares_minted", {
            "minter": caller,
            "amount": amount,
        })

    @external
    def swap_yes_market(self, buyer: Address, buy_long: Bool, amount: U128, min_out: U128) -> U128:
        """Swap YES_LONG for YES_SHORT (or vice versa) in the YES policy prediction pool.

        Args:
            buyer: Swapper.
            buy_long: True to swap SHORT -> LONG, False to swap LONG -> SHORT.
            amount: Input shares to swap.
            min_out: Slippage protection.
        """
        self._require_initialized()
        self._require_state(FutarchyState.PROPOSAL_ACTIVE)
        buyer.require_auth()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        res_long = self.storage.get("res_yes_long")
        res_short = self.storage.get("res_yes_short")

        out_amount = U128(0)

        if buy_long:
            # Input YES_SHORT, Output YES_LONG
            if self._get_balance(buyer, Symbol("YES_SHORT")) < amount:
                raise ContractError.INSUFFICIENT_BALANCE
            self._add_balance(buyer, Symbol("YES_SHORT"), U128(0) - amount)

            # dy = res_long * dx / (res_short + dx)
            out_amount = (res_long * amount) / (res_short + amount)
            if out_amount < min_out:
                raise ContractError.SLIPPAGE_EXCEEDED

            self.storage.set("res_yes_short", res_short + amount)
            self.storage.set("res_yes_long", res_long - out_amount)
            self._add_balance(buyer, Symbol("YES_LONG"), out_amount)
        else:
            # Input YES_LONG, Output YES_SHORT
            if self._get_balance(buyer, Symbol("YES_LONG")) < amount:
                raise ContractError.INSUFFICIENT_BALANCE
            self._add_balance(buyer, Symbol("YES_LONG"), U128(0) - amount)

            out_amount = (res_short * amount) / (res_long + amount)
            if out_amount < min_out:
                raise ContractError.SLIPPAGE_EXCEEDED

            self.storage.set("res_yes_long", res_long + amount)
            self.storage.set("res_yes_short", res_short - out_amount)
            self._add_balance(buyer, Symbol("YES_SHORT"), out_amount)

        self.env.emit_event("swapped", {
            "user": buyer,
            "market": Symbol("YES"),
            "buy_long": buy_long,
            "amount": amount,
            "out": out_amount,
        })

        return out_amount

    @external
    def swap_no_market(self, buyer: Address, buy_long: Bool, amount: U128, min_out: U128) -> U128:
        """Swap NO_LONG for NO_SHORT (or vice versa) in the NO policy prediction pool.

        Args:
            buyer: Swapper.
            buy_long: True to buy LONG, False to buy SHORT.
            amount: Input shares.
            min_out: Slippage control.
        """
        self._require_initialized()
        self._require_state(FutarchyState.PROPOSAL_ACTIVE)
        buyer.require_auth()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        res_long = self.storage.get("res_no_long")
        res_short = self.storage.get("res_no_short")

        out_amount = U128(0)

        if buy_long:
            if self._get_balance(buyer, Symbol("NO_SHORT")) < amount:
                raise ContractError.INSUFFICIENT_BALANCE
            self._add_balance(buyer, Symbol("NO_SHORT"), U128(0) - amount)

            out_amount = (res_long * amount) / (res_short + amount)
            if out_amount < min_out:
                raise ContractError.SLIPPAGE_EXCEEDED

            self.storage.set("res_no_short", res_short + amount)
            self.storage.set("res_no_long", res_long - out_amount)
            self._add_balance(buyer, Symbol("NO_LONG"), out_amount)
        else:
            if self._get_balance(buyer, Symbol("NO_LONG")) < amount:
                raise ContractError.INSUFFICIENT_BALANCE
            self._add_balance(buyer, Symbol("NO_LONG"), U128(0) - amount)

            out_amount = (res_short * amount) / (res_long + amount)
            if out_amount < min_out:
                raise ContractError.SLIPPAGE_EXCEEDED

            self.storage.set("res_no_long", res_long + amount)
            self.storage.set("res_no_short", res_short - out_amount)
            self._add_balance(buyer, Symbol("NO_SHORT"), out_amount)

        self.env.emit_event("swapped", {
            "user": buyer,
            "market": Symbol("NO"),
            "buy_long": buy_long,
            "amount": amount,
            "out": out_amount,
        })

        return out_amount

    @external
    def evaluate_decision(self, caller: Address) -> U64:
        """Compare predictions of both markets. The policy with the higher price is selected.
        The other market is voided and refunded.

        Args:
            caller: Trigger address.
        """
        self._require_initialized()
        self._require_state(FutarchyState.PROPOSAL_ACTIVE)
        caller.require_auth()

        now = self.env.ledger().timestamp()
        if now < self.storage.get("decision_deadline"):
            raise ContractError.DECISION_PERIOD_ACTIVE

        # Calculate implied prices (res_short / res_long + res_short)
        yes_l = self.storage.get("res_yes_long")
        yes_s = self.storage.get("res_yes_short")
        yes_price = (yes_s * U128(10000)) / (yes_l + yes_s)

        no_l = self.storage.get("res_no_long")
        no_s = self.storage.get("res_no_short")
        no_price = (no_s * U128(10000)) / (no_l + no_s)

        decision = U64(2) # Default Policy NO passed
        if yes_price > no_price:
            decision = U64(1) # Policy YES passed

        self.storage.set("decision", decision)
        self.storage.set("state", FutarchyState.DECISION_EVALUATED)
        self.storage.set("resolution_time", now + self.storage.get("impact_duration"))

        self.env.emit_event("decision_evaluated", {
            "decision": decision,
            "yes_price_bps": yes_price,
            "no_price_bps": no_price,
        })

        return decision

    @external
    def resolve_impact(self, caller: Address, metric_value: I128):
        """Set the final metric value for impact verification of the winning policy. Only oracle.

        Args:
            caller: Oracle.
            metric_value: The verified outcome value.
        """
        self._require_initialized()
        self._require_state(FutarchyState.DECISION_EVALUATED)
        caller.require_auth()

        oracle = self.storage.get("oracle")
        if caller != oracle:
            raise ContractError.UNAUTHORIZED

        now = self.env.ledger().timestamp()
        if now < self.storage.get("resolution_time"):
            raise ContractError.IMPACT_NOT_RESOLVED

        self.storage.set("final_metric_value", metric_value)
        self.storage.set("state", FutarchyState.RESOLVED)

        self.env.emit_event("impact_resolved", {
            "metric_value": metric_value,
        })

    @external
    def claim_payout(self, claimant: Address) -> U128:
        """Claim payout based on decision and final impact resolution.

        If Policy YES passed:
          - NO path shares are refunded at 0.5 collateral each (reclaims initial collateral).
          - YES path shares pay out proportionally based on final metric scalar value.
        If Policy NO passed:
          - YES path shares are refunded at 0.5 collateral each.
          - NO path shares pay out proportionally.
        """
        self._require_initialized()
        
        state = self.storage.get("state")
        if state != FutarchyState.RESOLVED:
            # Allow claiming refunds of the non-selected policy even during evaluation window
            if state == FutarchyState.DECISION_EVALUATED:
                pass
            else:
                raise ContractError.IMPACT_NOT_RESOLVED

        claimant.require_auth()

        decision = self.storage.get("decision")
        payout = U128(0)

        if decision == U64(1): # YES policy passed
            # Refund NO shares
            no_long = self._get_balance(claimant, Symbol("NO_LONG"))
            no_short = self._get_balance(claimant, Symbol("NO_SHORT"))
            payout = payout + (no_long + no_short) / U128(2)

            self.storage.set(("balance", claimant, Symbol("NO_LONG")), U128(0))
            self.storage.set(("balance", claimant, Symbol("NO_SHORT")), U128(0))

            if state == FutarchyState.RESOLVED:
                # YES shares pay out based on metric value
                yes_long = self._get_balance(claimant, Symbol("YES_LONG"))
                yes_short = self._get_balance(claimant, Symbol("YES_SHORT"))

                lower = self.storage.get("lower_bound")
                upper = self.storage.get("upper_bound")
                v = self.storage.get("final_metric_value")

                val = v
                if val < lower:
                    val = lower
                elif val > upper:
                    val = upper

                range_len = upper - lower
                long_weight = ((val - lower) * I128(1000000)) / range_len
                short_weight = I128(1000000) - long_weight

                payout_long = (yes_long * U128(long_weight)) / U128(1000000)
                payout_short = (yes_short * U128(short_weight)) / U128(1000000)

                payout = payout + payout_long + payout_short

                self.storage.set(("balance", claimant, Symbol("YES_LONG")), U128(0))
                self.storage.set(("balance", claimant, Symbol("YES_SHORT")), U128(0))

        else: # NO policy passed
            # Refund YES shares
            yes_long = self._get_balance(claimant, Symbol("YES_LONG"))
            yes_short = self._get_balance(claimant, Symbol("YES_SHORT"))
            payout = payout + (yes_long + yes_short) / U128(2)

            self.storage.set(("balance", claimant, Symbol("YES_LONG")), U128(0))
            self.storage.set(("balance", claimant, Symbol("YES_SHORT")), U128(0))

            if state == FutarchyState.RESOLVED:
                # NO shares pay out based on metric value
                no_long = self._get_balance(claimant, Symbol("NO_LONG"))
                no_short = self._get_balance(claimant, Symbol("NO_SHORT"))

                lower = self.storage.get("lower_bound")
                upper = self.storage.get("upper_bound")
                v = self.storage.get("final_metric_value")

                val = v
                if val < lower:
                    val = lower
                elif val > upper:
                    val = upper

                range_len = upper - lower
                long_weight = ((val - lower) * I128(1000000)) / range_len
                short_weight = I128(1000000) - long_weight

                payout_long = (no_long * U128(long_weight)) / U128(1000000)
                payout_short = (no_short * U128(short_weight)) / U128(1000000)

                payout = payout + payout_long + payout_short

                self.storage.set(("balance", claimant, Symbol("NO_LONG")), U128(0))
                self.storage.set(("balance", claimant, Symbol("NO_SHORT")), U128(0))

        if payout == U128(0):
            raise ContractError.ZERO_AMOUNT

        token = self.storage.get("token")
        self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), claimant, payout])

        self.env.emit_event("winnings_claimed", {
            "claimant": claimant,
            "payout": payout,
        })

        return payout

    @view
    def get_prices(self) -> Map:
        """Get prices for both YES and NO policies (scaled by 10000)."""
        yes_l = self.storage.get("res_yes_long")
        yes_s = self.storage.get("res_yes_short")
        yes_price = (yes_s * U128(10000)) / (yes_l + yes_s)

        no_l = self.storage.get("res_no_long")
        no_s = self.storage.get("res_no_short")
        no_price = (no_s * U128(10000)) / (no_l + no_s)

        res = Map()
        res.set("yes_policy_bps", yes_price)
        res.set("no_policy_bps", no_price)
        return res

    @view
    def get_state(self) -> Map:
        """Get state of decision."""
        res = Map()
        res.set("state", self.storage.get("state"))
        res.set("decision", self.storage.get("decision"))
        res.set("metric", self.storage.get("final_metric_value"))
        return res

    @view
    def get_user_balances(self, user: Address) -> Map:
        """Get user token balances."""
        res = Map()
        res.set("yes_long", self._get_balance(user, Symbol("YES_LONG")))
        res.set("yes_short", self._get_balance(user, Symbol("YES_SHORT")))
        res.set("no_long", self._get_balance(user, Symbol("NO_LONG")))
        res.set("no_short", self._get_balance(user, Symbol("NO_SHORT")))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_state(self, expected: U64):
        if self.storage.get("state") != expected:
            raise ContractError.DECISION_ALREADY_EVALUATED

    def _get_balance(self, user: Address, token_type: Symbol) -> U128:
        return self.storage.get(("balance", user, token_type), U128(0))

    def _add_balance(self, user: Address, token_type: Symbol, amount: U128):
        current = self._get_balance(user, token_type)
        self.storage.set(("balance", user, token_type), current + amount)
