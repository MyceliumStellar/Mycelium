"""
Gas Price Oracle — EMA smoothed gas feed with spike filtering and L1/L2 gas split tracking.

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
    INVALID_ALPHA = 4
    INVALID_MULTIPLIER = 5


# Limits
MAX_ALPHA_BPS = 10000  # 100%
MIN_ALPHA_BPS = 100    # 1%
MAX_MULTIPLIER = 100   # 100x


@contract
class GasPriceOracle:
    """Gas Price Oracle tracking L1/L2 gas splits and priority fees,
    smoothing values with an EMA to mitigate volatility and spikes."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        ema_alpha_bps: U64,
        max_increase_multiplier: U64,
        initial_l1_gas: U64,
        initial_l2_gas: U64,
        initial_priority: U64,
    ):
        """Initialize the gas oracle configurations."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        if ema_alpha_bps < MIN_ALPHA_BPS or ema_alpha_bps > MAX_ALPHA_BPS:
            raise ContractError.INVALID_ALPHA
        if max_increase_multiplier < U64(1) or max_increase_multiplier > MAX_MULTIPLIER:
            raise ContractError.INVALID_MULTIPLIER

        self.storage.set("admin", admin)
        self.storage.set("ema_alpha_bps", ema_alpha_bps)
        self.storage.set("max_increase_multiplier", max_increase_multiplier)
        
        # State
        self.storage.set("l1_gas", initial_l1_gas)
        self.storage.set("l1_gas_ema", initial_l1_gas)
        self.storage.set("l2_gas", initial_l2_gas)
        self.storage.set("l2_gas_ema", initial_l2_gas)
        self.storage.set("priority_fee", initial_priority)
        self.storage.set("priority_fee_ema", initial_priority)
        self.storage.set("last_update_time", self.env.ledger().timestamp())
        
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "ema_alpha_bps": ema_alpha_bps,
            "max_increase_multiplier": max_increase_multiplier,
        })

    @external
    def report_gas(
        self,
        reporter: Address,
        raw_l1_gas: U64,
        raw_l2_gas: U64,
        raw_priority_fee: U64,
        force: Bool,
    ):
        """Submit new gas price measurements. Only authorized reporters or Admin.

        Args:
            reporter: Reporter address.
            raw_l1_gas: Current L1 gas price (e.g. in Gwei or Native).
            raw_l2_gas: Current L2 gas price.
            raw_priority_fee: Current L2 priority fee.
            force: If True, bypasses spike filtering and overrides directly. Only allowed for admin.
        """
        self._require_initialized()
        reporter.require_auth()
        self._require_reporter(reporter)

        admin = self.storage.get("admin")
        is_admin = (reporter == admin)
        
        # Retrieve current EMAs
        l1_ema = self.storage.get("l1_gas_ema")
        l2_ema = self.storage.get("l2_gas_ema")
        priority_ema = self.storage.get("priority_fee_ema")
        
        # Spike filtering logic:
        # If the new value exceeds EMA * multiplier, cap it to prevent massive outlier blocks,
        # unless it is forced by admin.
        multiplier = self.storage.get("max_increase_multiplier")
        
        l1_val = raw_l1_gas
        l2_val = raw_l2_gas
        priority_val = raw_priority_fee

        if not (force and is_admin):
            # Check L1 spike
            max_l1 = l1_ema * multiplier
            if l1_val > max_l1:
                l1_val = max_l1
                self.env.emit_event("gas_spike_filtered", {"type": Symbol("l1"), "raw": raw_l1_gas, "capped": max_l1})

            # Check L2 spike
            max_l2 = l2_ema * multiplier
            if l2_val > max_l2:
                l2_val = max_l2
                self.env.emit_event("gas_spike_filtered", {"type": Symbol("l2"), "raw": raw_l2_gas, "capped": max_l2})

            # Check Priority spike
            max_priority = priority_ema * multiplier
            if priority_val > max_priority:
                priority_val = max_priority
                self.env.emit_event("gas_spike_filtered", {"type": Symbol("priority"), "raw": raw_priority_fee, "capped": max_priority})

        # Calculate EMA
        alpha = self.storage.get("ema_alpha_bps")
        
        next_l1_ema = self._calculate_ema(l1_val, l1_ema, alpha)
        next_l2_ema = self._calculate_ema(l2_val, l2_ema, alpha)
        next_priority_ema = self._calculate_ema(priority_val, priority_ema, alpha)

        # Update storage
        self.storage.set("l1_gas", l1_val)
        self.storage.set("l1_gas_ema", next_l1_ema)
        self.storage.set("l2_gas", l2_val)
        self.storage.set("l2_gas_ema", next_l2_ema)
        self.storage.set("priority_fee", priority_val)
        self.storage.set("priority_fee_ema", next_priority_ema)
        
        now = self.env.ledger().timestamp()
        self.storage.set("last_update_time", now)

        self.env.emit_event("gas_reported", {
            "l1_gas": l1_val,
            "l1_gas_ema": next_l1_ema,
            "l2_gas": l2_val,
            "l2_gas_ema": next_l2_ema,
            "timestamp": now,
        })

    # ------------------------------------------------------------------ #
    #  Admin Configurations                                               #
    # ------------------------------------------------------------------ #

    @external
    def add_reporter(self, admin: Address, reporter: Address):
        """Authorize a new reporter. Only Admin."""
        self._require_admin(admin)
        self.storage.set(("reporter", reporter), True)
        self.env.emit_event("reporter_added", {"reporter": reporter})

    @external
    def remove_reporter(self, admin: Address, reporter: Address):
        """Deauthorize a reporter. Only Admin."""
        self._require_admin(admin)
        self.storage.set(("reporter", reporter), False)
        self.env.emit_event("reporter_removed", {"reporter": reporter})

    @external
    def update_params(self, admin: Address, ema_alpha_bps: U64, max_increase_multiplier: U64):
        """Update configurations. Only Admin."""
        self._require_admin(admin)
        if ema_alpha_bps < MIN_ALPHA_BPS or ema_alpha_bps > MAX_ALPHA_BPS:
            raise ContractError.INVALID_ALPHA
        if max_increase_multiplier < U64(1) or max_increase_multiplier > MAX_MULTIPLIER:
            raise ContractError.INVALID_MULTIPLIER

        self.storage.set("ema_alpha_bps", ema_alpha_bps)
        self.storage.set("max_increase_multiplier", max_increase_multiplier)
        self.env.emit_event("params_updated", {
            "ema_alpha_bps": ema_alpha_bps,
            "max_increase_multiplier": max_increase_multiplier,
        })

    @external
    def transfer_admin(self, admin: Address, new_admin: Address):
        """Transfer admin role. Only Admin."""
        self._require_admin(admin)
        self.storage.set("admin", new_admin)
        self.env.emit_event("admin_transferred", {"old_admin": admin, "new_admin": new_admin})

    # ------------------------------------------------------------------ #
    #  View Functions                                                     #
    # ------------------------------------------------------------------ #

    @view
    def get_gas_prices(self) -> Map:
        """Get latest raw and EMA smoothed gas prices."""
        self._require_initialized()
        return {
            "l1_gas": self.storage.get("l1_gas"),
            "l1_gas_ema": self.storage.get("l1_gas_ema"),
            "l2_gas": self.storage.get("l2_gas"),
            "l2_gas_ema": self.storage.get("l2_gas_ema"),
            "priority_fee": self.storage.get("priority_fee"),
            "priority_fee_ema": self.storage.get("priority_fee_ema"),
            "last_update_time": self.storage.get("last_update_time"),
        }

    @view
    def get_l2_execution_gas_price(self) -> U64:
        """Get EMA smoothed L2 gas price + priority fee for execution estimate."""
        self._require_initialized()
        return self.storage.get("l2_gas_ema") + self.storage.get("priority_fee_ema")

    @view
    def is_authorized_reporter(self, reporter: Address) -> Bool:
        """Check if an address is an authorized reporter."""
        self._require_initialized()
        admin = self.storage.get("admin")
        if reporter == admin:
            return True
        return self.storage.get(("reporter", reporter), False)

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                   #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        caller.require_auth()
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_reporter(self, caller: Address):
        admin = self.storage.get("admin")
        if caller == admin:
            return
        if not self.storage.get(("reporter", caller), False):
            raise ContractError.UNAUTHORIZED

    def _calculate_ema(self, new_val: U64, old_ema: U64, alpha_bps: U64) -> U64:
        """Calculate Exponential Moving Average: (alpha * new + (10000 - alpha) * old) / 10000."""
        part_new = new_val * alpha_bps
        part_old = old_ema * (U64(10000) - alpha_bps)
        return (part_new + part_old) / U64(10000)
