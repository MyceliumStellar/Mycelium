"""
Flight Delay Insurance — Automated delay/cancellation parametric coverage.

Mycelium Smart Contract for Stellar
Provides instant parametric payouts based on flight delays and cancellations.
Supports flight policy purchase, oracle flight status updates, and graduated
payout levels depending on the duration of the delay.
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
    POLICY_ALREADY_EVALUATED = 6
    ORACLE_NOT_WHITELISTED = 7
    FLIGHT_DATA_MISSING = 8
    FLIGHT_NOT_DELAYED = 9
    INSUFFICIENT_POOL_BALANCE = 10
    POLICY_EXPIRED = 11


@contract
class FlightDelayInsurance:
    """
    Flight Delay Insurance offering instant payouts with graduated levels
    (e.g., 20% for 1h delay, 50% for 2h delay, 100% for 4h+ or cancellation).
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        asset_token: Address,
        min_premium: U128,
        premium_rate_bps: U64,  # e.g., 500 bps = 5% of coverage
    ):
        """Initialize the Flight Delay Insurance contract."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if min_premium == 0 or premium_rate_bps == 0:
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("admin", admin)
        self.storage.set("asset_token", asset_token)
        self.storage.set("min_premium", min_premium)
        self.storage.set("premium_rate_bps", premium_rate_bps)
        self.storage.set("policy_count", U64(0))
        self.storage.set("pool_balance", U128(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "asset_token": asset_token,
            "min_premium": min_premium,
        })

    @external
    def add_oracle(self, admin: Address, oracle: Address):
        """Whitelist an airline data oracle."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        self.storage.set(f"oracle:{oracle}:whitelisted", True)
        self.env.emit_event("oracle_added", {"oracle": oracle})

    @external
    def remove_oracle(self, admin: Address, oracle: Address):
        """Remove a whitelisted airline data oracle."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        self.storage.set(f"oracle:{oracle}:whitelisted", False)
        self.env.emit_event("oracle_removed", {"oracle": oracle})

    @external
    def deposit_pool(self, depositor: Address, amount: U128):
        """Deposit asset tokens to support coverage pool capacity."""
        depositor.require_auth()
        self._require_initialized()

        if amount == 0:
            raise ContractError.INVALID_PARAMETERS

        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, depositor, self.env.current_contract(), amount)

        pool_balance = self.storage.get("pool_balance", U128(0))
        self.storage.set("pool_balance", pool_balance + amount)

        self.env.emit_event("pool_deposited", {
            "depositor": depositor,
            "amount": amount,
        })

    @external
    def withdraw_pool(self, admin: Address, recipient: Address, amount: U128):
        """Withdraw unused capital from the pool."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        pool_balance = self.storage.get("pool_balance", U128(0))
        if amount > pool_balance:
            raise ContractError.INSUFFICIENT_POOL_BALANCE

        self.storage.set("pool_balance", pool_balance - amount)
        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, self.env.current_contract(), recipient, amount)

        self.env.emit_event("pool_withdrawn", {
            "recipient": recipient,
            "amount": amount,
        })

    @external
    def buy_policy(
        self,
        buyer: Address,
        flight_id: Symbol,
        scheduled_departure_ledger: U64,
        coverage_amount: U128,
    ) -> U64:
        """Buy a delay insurance policy for a specific flight."""
        buyer.require_auth()
        self._require_initialized()

        current_ledger = self.env.ledger().sequence()
        # Must purchase policy before scheduled flight departure (e.g. at least 500 ledgers before)
        if current_ledger + U64(500) > scheduled_departure_ledger:
            raise ContractError.INVALID_PARAMETERS

        premium_rate = self.storage.get("premium_rate_bps")
        premium = (coverage_amount * U128(premium_rate)) // U128(10000)

        min_premium = self.storage.get("min_premium")
        if premium < min_premium:
            premium = min_premium

        # Collect premium
        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, buyer, self.env.current_contract(), premium)

        # Update pool balance
        pool_balance = self.storage.get("pool_balance", U128(0))
        self.storage.set("pool_balance", pool_balance + premium)

        policy_id = self.storage.get("policy_count") + 1
        self.storage.set("policy_count", policy_id)

        self.storage.set(f"policy:{policy_id}:holder", buyer)
        self.storage.set(f"policy:{policy_id}:flight_id", flight_id)
        self.storage.set(f"policy:{policy_id}:scheduled", scheduled_departure_ledger)
        self.storage.set(f"policy:{policy_id}:coverage", coverage_amount)
        self.storage.set(f"policy:{policy_id}:premium", premium)
        self.storage.set(f"policy:{policy_id}:evaluated", False)

        self.env.emit_event("policy_purchased", {
            "policy_id": policy_id,
            "holder": buyer,
            "flight_id": flight_id,
            "coverage": coverage_amount,
            "premium": premium,
            "scheduled": scheduled_departure_ledger,
        })

        return policy_id

    @external
    def report_flight_status(
        self,
        oracle: Address,
        flight_id: Symbol,
        departure_ledger: U64,
        actual_departure_ledger: U64,
        is_cancelled: Bool,
    ):
        """Oracle reports departure metrics or cancellation for a flight."""
        oracle.require_auth()
        self._require_initialized()

        if not self.storage.get(f"oracle:{oracle}:whitelisted", False):
            raise ContractError.ORACLE_NOT_WHITELISTED

        # Record flight status
        prefix = f"flight:{flight_id}:{departure_ledger}"
        self.storage.set(f"{prefix}:actual", actual_departure_ledger)
        self.storage.set(f"{prefix}:cancelled", is_cancelled)
        self.storage.set(f"{prefix}:reported", True)

        self.env.emit_event("flight_status_reported", {
            "flight_id": flight_id,
            "departure_ledger": departure_ledger,
            "actual_departure": actual_departure_ledger,
            "is_cancelled": is_cancelled,
        })

    @external
    def evaluate_policy(self, caller: Address, policy_id: U64):
        """Instantly evaluate flight delay policies once status is reported by oracle."""
        caller.require_auth()
        self._require_initialized()

        holder = self.storage.get(f"policy:{policy_id}:holder", None)
        if holder is None:
            raise ContractError.POLICY_NOT_FOUND

        if self.storage.get(f"policy:{policy_id}:evaluated", False):
            raise ContractError.POLICY_ALREADY_EVALUATED

        flight_id = self.storage.get(f"policy:{policy_id}:flight_id")
        scheduled = self.storage.get(f"policy:{policy_id}:scheduled")

        prefix = f"flight:{flight_id}:{scheduled}"
        if not self.storage.get(f"{prefix}:reported", False):
            raise ContractError.FLIGHT_DATA_MISSING

        actual = self.storage.get(f"{prefix}:actual")
        is_cancelled = self.storage.get(f"{prefix}:cancelled")

        coverage = self.storage.get(f"policy:{policy_id}:coverage")
        payout_bps = U64(0)

        if is_cancelled:
            payout_bps = U64(10000)  # 100% payout for cancellation
        elif actual > scheduled:
            delay = actual - scheduled
            # Delay thresholds in ledgers (assuming ~5 seconds per ledger on Stellar)
            # 1 hour = 720 ledgers
            # 2 hours = 1440 ledgers
            # 4 hours = 2880 ledgers
            if delay >= 2880:
                payout_bps = U64(10000)  # 100%
            elif delay >= 1440:
                payout_bps = U64(5000)   # 50%
            elif delay >= 720:
                payout_bps = U64(2000)   # 20%

        if payout_bps == 0:
            # Flight was on time or delay was negligible. Close the policy without payout.
            self.storage.set(f"policy:{policy_id}:evaluated", True)
            self.storage.set(f"policy:{policy_id}:payout", U128(0))
            self.env.emit_event("policy_evaluated_no_payout", {"policy_id": policy_id})
            return

        payout_amount = (coverage * U128(payout_bps)) // U128(10000)
        pool_balance = self.storage.get("pool_balance", U128(0))

        if payout_amount > 0:
            if payout_amount > pool_balance:
                # Max payout bounded by pool solvency
                payout_amount = pool_balance

            self.storage.set("pool_balance", pool_balance - payout_amount)
            asset_token = self.storage.get("asset_token")
            self.env.transfer(asset_token, self.env.current_contract(), holder, payout_amount)

        self.storage.set(f"policy:{policy_id}:evaluated", True)
        self.storage.set(f"policy:{policy_id}:payout", payout_amount)

        self.env.emit_event("policy_evaluated", {
            "policy_id": policy_id,
            "holder": holder,
            "payout_amount": payout_amount,
            "satisfied_bps": payout_bps,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_policy(self, policy_id: U64) -> Map:
        """Get flight delay policy details."""
        holder = self.storage.get(f"policy:{policy_id}:holder", None)
        if holder is None:
            raise ContractError.POLICY_NOT_FOUND

        return {
            "policy_id": policy_id,
            "holder": holder,
            "flight_id": self.storage.get(f"policy:{policy_id}:flight_id"),
            "scheduled": self.storage.get(f"policy:{policy_id}:scheduled"),
            "coverage": self.storage.get(f"policy:{policy_id}:coverage"),
            "premium": self.storage.get(f"policy:{policy_id}:premium"),
            "evaluated": self.storage.get(f"policy:{policy_id}:evaluated"),
            "payout": self.storage.get(f"policy:{policy_id}:payout", U128(0)),
        }

    @view
    def get_flight_status(self, flight_id: Symbol, scheduled_departure: U64) -> Map:
        """Retrieve reported status for a flight scheduled departure."""
        prefix = f"flight:{flight_id}:{scheduled_departure}"
        if not self.storage.get(f"{prefix}:reported", False):
            raise ContractError.FLIGHT_DATA_MISSING

        return {
            "actual": self.storage.get(f"{prefix}:actual"),
            "cancelled": self.storage.get(f"{prefix}:cancelled"),
        }

    @view
    def get_pool_balance(self) -> U128:
        """Get current pool reserve balance."""
        return self.storage.get("pool_balance", U128(0))

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED
