"""
CircuitBreaker — Global status toggles, threshold alerts, automatic pausing rules, restoration timers.

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
    RESTORATION_DELAY_ACTIVE = 4
    NOT_TRIPPED = 5
    ALREADY_TRIPPED = 6
    INVALID_THRESHOLD = 7

@contract
class CircuitBreaker:
    """
    Emergency circuit breaker and pause controller.
    
    Protects protocol components by tracking failure rates and automatically
    pausing integrations. Enforces cooling-off restoration timers before resetting.
    """
    
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self, 
        admin: Address, 
        min_restoration_delay: U64, 
        alert_threshold: U64, 
        error_window: U64
    ):
        """
        Initializes the circuit breaker.
        
        Args:
            admin: Admin address controlling overrides and whitelist.
            min_restoration_delay: Seconds that must pass since tripping before unpausing.
            alert_threshold: Error threshold count within a window to auto-trip.
            error_window: Sliding timeframe window in seconds to monitor failures.
        """
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED
            
        if alert_threshold == U64(0) or error_window == U64(0):
            raise ContractError.INVALID_THRESHOLD
            
        self.storage.set("admin", admin)
        self.storage.set("min_restore_delay", min_restoration_delay)
        self.storage.set("alert_threshold", alert_threshold)
        self.storage.set("error_window", error_window)
        
        # State indicators
        self.storage.set("is_tripped", False)
        self.storage.set("trip_reason", Symbol("NONE"))
        self.storage.set("trip_time", U64(0))
        
        # Diagnostics
        self.storage.set("err_count", U64(0))
        self.storage.set("win_start", self.env.ledger().timestamp())
        self.storage.set("initialized", True)
        
        self.env.emit_event(
            "initialized", 
            {
                "admin": admin, 
                "restore_delay": min_restoration_delay, 
                "threshold": alert_threshold, 
                "window": error_window
            }
        )

    @external
    def trip(self, caller: Address, reason: Symbol) -> Bool:
        """
        Manually trips the circuit breaker immediately.
        
        Args:
            caller: Authorized account (admin or monitoring agent).
            reason: Event summary code.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin_or_monitor(caller)
        
        if self.storage.get("is_tripped", False):
            raise ContractError.ALREADY_TRIPPED
            
        current_time = self.env.ledger().timestamp()
        self.storage.set("is_tripped", True)
        self.storage.set("trip_reason", reason)
        self.storage.set("trip_time", current_time)
        
        self.env.emit_event("circuit_tripped", {"reason": reason, "by": caller, "at": current_time})
        return True

    @external
    def report_error(self, caller: Address) -> Bool:
        """
        Reports an error event.
        
        If error count within the current window exceeds threshold, auto-trips.
        """
        caller.require_auth()
        self._require_initialized()
        
        # Skip count increments if already tripped
        if self.storage.get("is_tripped", False):
            return False
            
        current_time = self.env.ledger().timestamp()
        win_start = self.storage.get("win_start", U64(0))
        window = self.storage.get("error_window", U64(0))
        err_count = self.storage.get("err_count", U64(0))
        
        # Check window expiration
        if current_time >= win_start + window:
            err_count = U64(1)
            self.storage.set("win_start", current_time)
        else:
            err_count += U64(1)
            
        self.storage.set("err_count", err_count)
        
        # Check threshold trigger
        threshold = self.storage.get("alert_threshold", U64(0))
        if err_count >= threshold:
            self.storage.set("is_tripped", True)
            self.storage.set("trip_reason", Symbol("AUTO_THRESHOLD_EXCEEDED"))
            self.storage.set("trip_time", current_time)
            
            self.env.emit_event(
                "circuit_tripped", 
                {"reason": Symbol("AUTO_THRESHOLD_EXCEEDED"), "by": caller, "at": current_time}
            )
            return True
            
        return False

    @external
    def reset_breaker(self, caller: Address) -> Bool:
        """
        Resets/unpauses the circuit breaker after restoration delay has elapsed.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        if not self.storage.get("is_tripped", False):
            raise ContractError.NOT_TRIPPED
            
        # Verify restoration delay
        trip_time = self.storage.get("trip_time", U64(0))
        delay = self.storage.get("min_restore_delay", U64(0))
        current_time = self.env.ledger().timestamp()
        
        if current_time < trip_time + delay:
            raise ContractError.RESTORATION_DELAY_ACTIVE
            
        # Reset parameters
        self.storage.set("is_tripped", False)
        self.storage.set("trip_reason", Symbol("NONE"))
        self.storage.set("err_count", U64(0))
        self.storage.set("win_start", current_time)
        
        self.env.emit_event("circuit_reset", {"by": caller, "at": current_time})
        return True

    @external
    def set_whitelisted_address(self, caller: Address, target: Address, whitelisted: Bool) -> Bool:
        """
        Permits or removes key addresses from bypass rules when tripped.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        self.storage.set("whitelist:" + str(target), whitelisted)
        self.env.emit_event("whitelist_updated", {"target": target, "whitelisted": whitelisted})
        return True

    @external
    def set_monitoring_address(self, caller: Address, monitor: Address, allowed: Bool) -> Bool:
        """
        Grants or revokes a third-party monitor address the right to trip the breaker.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        self.storage.set("monitor:" + str(monitor), allowed)
        self.env.emit_event("monitor_updated", {"monitor": monitor, "allowed": allowed})
        return True

    @view
    def is_paused(self, caller: Address) -> Bool:
        """
        Indicates if execution is currently halted.
        
        Bypassed if target caller is explicitly whitelisted.
        """
        self._require_initialized()
        if not self.storage.get("is_tripped", False):
            return False
            
        # Check whitelist bypass
        if self.storage.get("whitelist:" + str(caller), False):
            return False
            
        return True

    @view
    def get_breaker_status(self) -> Map:
        """
        Returns full diagnostics and operational status of the breaker.
        """
        self._require_initialized()
        status = Map()
        status.set(Symbol("is_tripped"), self.storage.get("is_tripped", False))
        status.set(Symbol("trip_reason"), self.storage.get("trip_reason"))
        status.set(Symbol("trip_time"), self.storage.get("trip_time"))
        status.set(Symbol("min_restore_delay"), self.storage.get("min_restore_delay", U64(0)))
        status.set(Symbol("current_errors"), self.storage.get("err_count", U64(0)))
        return status

    # Internal helpers
    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_admin_or_monitor(self, caller: Address):
        admin = self.storage.get("admin")
        if caller == admin:
            return
        if self.storage.get("monitor:" + str(caller), False):
            return
        raise ContractError.UNAUTHORIZED
