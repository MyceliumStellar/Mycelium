"""
Parametric Insurance — Automated insurance based on multi-condition oracle triggers.

Mycelium Smart Contract for Stellar
Provides automated policy creation and execution based on verifiable parameters
reported by whitelisted oracles (e.g. weather, price feeds, flight delays).
Claims do not require human claims adjusters; instead, they are evaluated
computationally based on the multi-condition triggers set up at policy creation.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    INVALID_PARAMETERS = 4
    POLICY_NOT_FOUND = 5
    POLICY_EXPIRED = 6
    POLICY_NOT_EXPIRED = 7
    POLICY_ALREADY_EVALUATED = 8
    ORACLE_NOT_WHITELISTED = 9
    INSUFFICIENT_POOL_BALANCE = 10
    INSUFFICIENT_FUNDS = 11
    INVALID_CONDITION = 12
    NO_CONDITIONS_MET = 13
    CONDITIONS_NOT_MET = 14
    TRANSFER_FAILED = 15


class ComparisonOperator:
    GREATER_THAN = 1
    LESS_THAN = 2
    EQUAL = 3
    NOT_EQUAL = 4


@contract
class ParametricInsurance:
    """
    Parametric Insurance contract offering automated payouts based on multi-condition oracle triggers.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        asset_token: Address,
        min_duration_ledgers: U64,
        max_duration_ledgers: U64,
        min_coverage: U128,
        max_coverage: U128,
    ):
        """Initialize the parametric insurance contract."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if min_duration_ledgers == 0 or min_duration_ledgers > max_duration_ledgers:
            raise ContractError.INVALID_PARAMETERS
        if min_coverage == 0 or min_coverage > max_coverage:
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("admin", admin)
        self.storage.set("asset_token", asset_token)
        self.storage.set("min_duration_ledgers", min_duration_ledgers)
        self.storage.set("max_duration_ledgers", max_duration_ledgers)
        self.storage.set("min_coverage", min_coverage)
        self.storage.set("max_coverage", max_coverage)
        self.storage.set("policy_count", U64(0))
        self.storage.set("pool_balance", U128(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "asset_token": asset_token,
        })

    @external
    def add_oracle(self, admin: Address, oracle: Address):
        """Whitelist a trusted data oracle."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        self.storage.set(f"oracle:{oracle}:whitelisted", True)
        self.env.emit_event("oracle_added", {"oracle": oracle})

    @external
    def remove_oracle(self, admin: Address, oracle: Address):
        """Remove a trusted data oracle from whitelist."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        self.storage.set(f"oracle:{oracle}:whitelisted", False)
        self.env.emit_event("oracle_removed", {"oracle": oracle})

    @external
    def deposit_pool(self, depositor: Address, amount: U128):
        """Deposit asset tokens to fund coverage payouts."""
        depositor.require_auth()
        self._require_initialized()

        if amount == 0:
            raise ContractError.INVALID_PARAMETERS

        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, depositor, self.env.current_contract(), amount)

        balance = self.storage.get("pool_balance", U128(0))
        self.storage.set("pool_balance", balance + amount)

        self.env.emit_event("pool_deposited", {
            "depositor": depositor,
            "amount": amount,
        })

    @external
    def withdraw_pool(self, admin: Address, recipient: Address, amount: U128):
        """Withdraw asset tokens from the pool balance."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        balance = self.storage.get("pool_balance", U128(0))
        if amount > balance:
            raise ContractError.INSUFFICIENT_POOL_BALANCE

        self.storage.set("pool_balance", balance - amount)
        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, self.env.current_contract(), recipient, amount)

        self.env.emit_event("pool_withdrawn", {
            "recipient": recipient,
            "amount": amount,
        })

    @external
    def report_metric(self, oracle: Address, metric_key: Bytes, value: I128):
        """Oracle reports a metric value to the contract."""
        oracle.require_auth()
        self._require_initialized()

        if not self.storage.get(f"oracle:{oracle}:whitelisted", False):
            raise ContractError.ORACLE_NOT_WHITELISTED

        current_ledger = self.env.ledger().sequence()
        self.storage.set(f"metric:{oracle}:{metric_key}:value", value)
        self.storage.set(f"metric:{oracle}:{metric_key}:ledger", current_ledger)

        self.env.emit_event("metric_reported", {
            "oracle": oracle,
            "metric_key": metric_key,
            "value": value,
            "ledger": current_ledger,
        })

    @external
    def create_policy(
        self,
        buyer: Address,
        coverage_amount: U128,
        duration_ledgers: U64,
        conditions: Vec,
    ) -> U64:
        """
        Create a parametric policy with customized conditions.
        Each condition in Vec is a Map containing:
        - "oracle": Address
        - "metric_key": Bytes
        - "operator": U64 (ComparisonOperator)
        - "threshold": I128
        - "weight": U64 (payout weight in bps, e.g. 5000 for 50%, total must <= 10000)
        """
        buyer.require_auth()
        self._require_initialized()

        min_cov = self.storage.get("min_coverage")
        max_cov = self.storage.get("max_coverage")
        if coverage_amount < min_cov or coverage_amount > max_cov:
            raise ContractError.INVALID_PARAMETERS

        min_dur = self.storage.get("min_duration_ledgers")
        max_dur = self.storage.get("max_duration_ledgers")
        if duration_ledgers < min_dur or duration_ledgers > max_dur:
            raise ContractError.INVALID_PARAMETERS

        conditions_len = len(conditions)
        if conditions_len == 0:
            raise ContractError.INVALID_CONDITION

        # Validate conditions and sum premium weights
        total_weight = U64(0)
        for i in range(conditions_len):
            cond = conditions[i]
            oracle = cond.get("oracle")
            operator = cond.get("operator")
            weight = cond.get("weight")

            if not self.storage.get(f"oracle:{oracle}:whitelisted", False):
                raise ContractError.ORACLE_NOT_WHITELISTED
            if operator not in (
                ComparisonOperator.GREATER_THAN,
                ComparisonOperator.LESS_THAN,
                ComparisonOperator.EQUAL,
                ComparisonOperator.NOT_EQUAL,
            ):
                raise ContractError.INVALID_CONDITION
            if weight == 0 or weight > 10000:
                raise ContractError.INVALID_CONDITION

            total_weight += weight

        if total_weight > 10000:
            raise ContractError.INVALID_CONDITION

        # Calculate premium: premium = coverage * weight_factor * duration_factor
        # Simplified: base premium of 1% coverage per 100,000 ledgers, scaled by total weight
        premium = (coverage_amount * U128(duration_ledgers) * U128(total_weight)) // U128(100000000)
        if premium == 0:
            premium = U128(1)

        # Collect premium
        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, buyer, self.env.current_contract(), premium)

        # Record policy
        policy_id = self.storage.get("policy_count") + 1
        self.storage.set("policy_count", policy_id)

        current_ledger = self.env.ledger().sequence()
        expiry_ledger = current_ledger + duration_ledgers

        self.storage.set(f"policy:{policy_id}:holder", buyer)
        self.storage.set(f"policy:{policy_id}:coverage", coverage_amount)
        self.storage.set(f"policy:{policy_id}:premium", premium)
        self.storage.set(f"policy:{policy_id}:expiry", expiry_ledger)
        self.storage.set(f"policy:{policy_id}:evaluated", False)
        self.storage.set(f"policy:{policy_id}:conditions_count", U64(conditions_len))

        for idx in range(conditions_len):
            cond = conditions[idx]
            self.storage.set(f"policy:{policy_id}:condition:{idx}:oracle", cond.get("oracle"))
            self.storage.set(f"policy:{policy_id}:condition:{idx}:metric_key", cond.get("metric_key"))
            self.storage.set(f"policy:{policy_id}:condition:{idx}:operator", cond.get("operator"))
            self.storage.set(f"policy:{policy_id}:condition:{idx}:threshold", cond.get("threshold"))
            self.storage.set(f"policy:{policy_id}:condition:{idx}:weight", cond.get("weight"))

        # Add premium to pool balance
        pool_bal = self.storage.get("pool_balance", U128(0))
        self.storage.set("pool_balance", pool_bal + premium)

        self.env.emit_event("policy_created", {
            "policy_id": policy_id,
            "holder": buyer,
            "coverage": coverage_amount,
            "premium": premium,
            "expiry": expiry_ledger,
        })

        return policy_id

    @external
    def evaluate_policy(self, caller: Address, policy_id: U64):
        """
        Auto-evaluate a policy against the latest oracle metrics.
        Can be called by anyone. Payout is determined by satisfied trigger conditions.
        """
        caller.require_auth()
        self._require_initialized()

        holder = self.storage.get(f"policy:{policy_id}:holder", None)
        if holder is None:
            raise ContractError.POLICY_NOT_FOUND

        if self.storage.get(f"policy:{policy_id}:evaluated", False):
            raise ContractError.POLICY_ALREADY_EVALUATED

        current_ledger = self.env.ledger().sequence()
        expiry = self.storage.get(f"policy:{policy_id}:expiry")
        # Allow evaluation at any time, but policy metrics must be within bounds or before expiry.
        # Once expired, evaluation checks conditions based on last recorded oracle values.

        conditions_count = self.storage.get(f"policy:{policy_id}:conditions_count", U64(0))
        total_payout_weight_bps = U64(0)

        for idx in range(conditions_count):
            oracle = self.storage.get(f"policy:{policy_id}:condition:{idx}:oracle")
            metric_key = self.storage.get(f"policy:{policy_id}:condition:{idx}:metric_key")
            operator = self.storage.get(f"policy:{policy_id}:condition:{idx}:operator")
            threshold = self.storage.get(f"policy:{policy_id}:condition:{idx}:threshold")
            weight = self.storage.get(f"policy:{policy_id}:condition:{idx}:weight")

            # Get current reported metric value
            metric_val = self.storage.get(f"metric:{oracle}:{metric_key}:value", None)
            if metric_val is None:
                continue  # Oracle hasn't reported yet, skip this condition

            # Check if metric was reported before expiry
            report_ledger = self.storage.get(f"metric:{oracle}:{metric_key}:ledger", U64(0))
            if report_ledger > expiry:
                # Oracle report after policy expiry is invalid for this evaluation
                continue

            # Evaluate condition
            condition_met = False
            if operator == ComparisonOperator.GREATER_THAN:
                condition_met = metric_val > threshold
            elif operator == ComparisonOperator.LESS_THAN:
                condition_met = metric_val < threshold
            elif operator == ComparisonOperator.EQUAL:
                condition_met = metric_val == threshold
            elif operator == ComparisonOperator.NOT_EQUAL:
                condition_met = metric_val != threshold

            if condition_met:
                total_payout_weight_bps += weight

        if total_payout_weight_bps == 0:
            # If the policy is expired, we mark it evaluated so it can't be spammed
            if current_ledger > expiry:
                self.storage.set(f"policy:{policy_id}:evaluated", True)
                self.env.emit_event("policy_evaluated_expired", {"policy_id": policy_id})
                return
            else:
                raise ContractError.CONDITIONS_NOT_MET

        # Calculate payout
        coverage = self.storage.get(f"policy:{policy_id}:coverage")
        payout_amount = (coverage * U128(total_payout_weight_bps)) // U128(10000)

        if payout_amount > 0:
            pool_balance = self.storage.get("pool_balance", U128(0))
            if payout_amount > pool_balance:
                # Pool is insolvent or has insufficient funds. Transfer maximum possible.
                payout_amount = pool_balance

            if payout_amount > 0:
                self.storage.set("pool_balance", pool_balance - payout_amount)
                asset_token = self.storage.get("asset_token")
                self.env.transfer(asset_token, self.env.current_contract(), holder, payout_amount)

        self.storage.set(f"policy:{policy_id}:evaluated", True)
        self.storage.set(f"policy:{policy_id}:payout_amount", payout_amount)

        self.env.emit_event("policy_evaluated", {
            "policy_id": policy_id,
            "holder": holder,
            "payout_amount": payout_amount,
            "satisfied_bps": total_payout_weight_bps,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_policy(self, policy_id: U64) -> Map:
        """Retrieve policy information and its conditions."""
        holder = self.storage.get(f"policy:{policy_id}:holder", None)
        if holder is None:
            raise ContractError.POLICY_NOT_FOUND

        conditions_count = self.storage.get(f"policy:{policy_id}:conditions_count", U64(0))
        cond_list = Vec()
        for idx in range(conditions_count):
            cond_list.append({
                "oracle": self.storage.get(f"policy:{policy_id}:condition:{idx}:oracle"),
                "metric_key": self.storage.get(f"policy:{policy_id}:condition:{idx}:metric_key"),
                "operator": self.storage.get(f"policy:{policy_id}:condition:{idx}:operator"),
                "threshold": self.storage.get(f"policy:{policy_id}:condition:{idx}:threshold"),
                "weight": self.storage.get(f"policy:{policy_id}:condition:{idx}:weight"),
            })

        return {
            "policy_id": policy_id,
            "holder": holder,
            "coverage": self.storage.get(f"policy:{policy_id}:coverage"),
            "premium": self.storage.get(f"policy:{policy_id}:premium"),
            "expiry": self.storage.get(f"policy:{policy_id}:expiry"),
            "evaluated": self.storage.get(f"policy:{policy_id}:evaluated"),
            "payout_amount": self.storage.get(f"policy:{policy_id}:payout_amount", U128(0)),
            "conditions": cond_list,
        }

    @view
    def get_metric(self, oracle: Address, metric_key: Bytes) -> Map:
        """Get the latest reported value and ledger for a metric."""
        value = self.storage.get(f"metric:{oracle}:{metric_key}:value", None)
        ledger = self.storage.get(f"metric:{oracle}:{metric_key}:ledger", U64(0))
        return {
            "value": value,
            "ledger": ledger,
        }

    @view
    def is_oracle_whitelisted(self, oracle: Address) -> Bool:
        """Check if an oracle is whitelisted."""
        return self.storage.get(f"oracle:{oracle}:whitelisted", False)

    @view
    def get_pool_balance(self) -> U128:
        """Retrieve total pool balance."""
        return self.storage.get("pool_balance", U128(0))

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED
