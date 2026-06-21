"""
Session Key Manager — Temporary session keys, scoped calls, and rate limiters.

Mycelium Smart Contract for Stellar. Authorizes temporary session keys to transact
on behalf of a primary address, enforces scopes on target contracts/functions,
constrains total call limits and window-based rate limits, and handles expiration timeouts.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)

class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    PAUSED = 4
    SESSION_NOT_FOUND = 5
    SESSION_EXPIRED = 6
    SCOPE_NOT_ALLOWED = 7
    CALL_LIMIT_REACHED = 8
    RATE_LIMIT_EXCEEDED = 9

@contract
class SessionKeyManager:
    """
    Stellar session key manager.
    Enables low-friction transactions (like in gaming or automated microtransactions)
    without sacrificing safety parameters.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """Initialize the session key manager."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin})

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause session executions."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- SESSION REGISTRATION ---

    @external
    def register_session(
        self,
        caller: Address,
        session_key: Address,
        duration_sec: U64,
        allowed_contracts: Vec,
        allowed_methods: Vec,
        call_limit: U64,
        rate_limit_per_minute: U64
    ):
        """
        Register a temporary session key mapped to caller.
        
        Args:
            caller: Primary wallet address delegating permissions.
            session_key: Ephemeral/Session key address.
            duration_sec: Session active lifetime in seconds (e.g. 1 hour = 3600).
            allowed_contracts: Vector of target contract addresses.
            allowed_methods: Vector of allowed method symbols (parallel indices with contracts).
            call_limit: Total absolute calls allowed.
            rate_limit_per_minute: Maximum calls permitted per 60-second window.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if duration_sec == U64(0):
            raise ContractError.SESSION_EXPIRED

        # Ensure scopes length match
        if len(allowed_contracts) != len(allowed_methods):
            raise ContractError.SCOPE_NOT_ALLOWED

        expiry = self._get_now() + duration_sec

        # Record session configuration
        self.storage.set(f"session_owner_{session_key}", caller)
        self.storage.set(f"session_expiry_{session_key}", expiry)
        self.storage.set(f"session_call_limit_{session_key}", call_limit)
        self.storage.set(f"session_calls_made_{session_key}", U64(0))
        self.storage.set(f"session_rate_limit_{session_key}", rate_limit_per_minute)
        self.storage.set(f"session_window_start_{session_key}", self._get_now())
        self.storage.set(f"session_window_calls_{session_key}", U64(0))

        # Record scope mappings
        self.storage.set(f"scopes_len_{session_key}", len(allowed_contracts))
        for i in range(len(allowed_contracts)):
            contract_addr = allowed_contracts.get(i)
            method = allowed_methods.get(i)
            self.storage.set(f"scope_contract_{session_key}_{i}", contract_addr)
            self.storage.set(f"scope_method_{session_key}_{i}", method)

        self.env.emit_event("session_registered", {
            "owner": caller,
            "session_key": session_key,
            "expiry": expiry
        })

    @external
    def revoke_session(self, caller: Address, session_key: Address):
        """Revoke a session key instantly. Can be called by owner or session key itself."""
        caller.require_auth()
        self._require_initialized()

        owner = self.storage.get(f"session_owner_{session_key}")
        if owner is None:
            raise ContractError.SESSION_NOT_FOUND

        if caller != owner and caller != session_key:
            raise ContractError.UNAUTHORIZED

        self._cleanup_session_state(session_key)
        self.env.emit_event("session_revoked", {"session_key": session_key, "owner": owner})

    # --- SESSION EXECUTION BRIDGE ---

    @external
    def execute_session_call(
        self,
        caller: Address,
        target_contract: Address,
        method_name: Symbol,
        arguments: Vec
    ):
        """
        Execute an authorized action on behalf of primary owner using session key credentials.
        
        Args:
            caller: Ephemeral session key address. Must authorize the call.
            target_contract: The contract receiving the call.
            method_name: Contract function name.
            arguments: List of parameters to route.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        # Validate session configuration
        owner = self.storage.get(f"session_owner_{caller}")
        if owner is None:
            raise ContractError.SESSION_NOT_FOUND

        # Expiry check
        expiry = self.storage.get(f"session_expiry_{caller}", U64(0))
        now = self._get_now()
        if now >= expiry:
            self._cleanup_session_state(caller)
            raise ContractError.SESSION_EXPIRED

        # Total call limits check
        calls_made = self.storage.get(f"session_calls_made_{caller}", U64(0))
        limit = self.storage.get(f"session_call_limit_{caller}", U64(0))
        if calls_made >= limit:
            self._cleanup_session_state(caller)
            raise ContractError.CALL_LIMIT_REACHED

        # Verify function scopes
        self._require_allowed_scope(caller, target_contract, method_name)

        # Rate limits check
        self._apply_rate_limits(caller, now)

        # Update limits
        self.storage.set(f"session_calls_made_{caller}", calls_made + U64(1))

        # Invoke targeted call. Pass owner address as first parameter or caller context parameter
        # To make it realistic, the target contract has to verify the caller is either the user
        # or the SessionKeyManager executing on behalf of user.
        # We invoke: target_contract.method(owner, args...)
        # We construct arguments list prepend with owner
        routed_args = Vec(self.env)
        routed_args.push_back(owner)
        for i in range(len(arguments)):
            routed_args.push_back(arguments.get(i))

        # Route actual contract call
        self.env.call(target_contract, method_name.to_string(), routed_args)

        self.env.emit_event("session_call_executed", {
            "session_key": caller,
            "owner": owner,
            "target": target_contract,
            "method": method_name
        })

    # --- VIEWS ---

    @view
    def get_session_info(self, session_key: Address) -> Map:
        """Inspect active session parameters."""
        self._require_initialized()
        res = Map(self.env)
        owner = self.storage.get(f"session_owner_{session_key}")
        if owner is not None:
            res.set("owner", owner)
            res.set("expiry", self.storage.get(f"session_expiry_{session_key}"))
            res.set("calls_made", self.storage.get(f"session_calls_made_{session_key}"))
            res.set("limit", self.storage.get(f"session_call_limit_{session_key}"))
        return res

    @view
    def is_session_active(self, session_key: Address) -> Bool:
        """Helper to quickly check if session key is active and valid."""
        owner = self.storage.get(f"session_owner_{session_key}")
        if owner is None:
            return False

        expiry = self.storage.get(f"session_expiry_{session_key}", U64(0))
        if self._get_now() >= expiry:
            return False

        calls = self.storage.get(f"session_calls_made_{session_key}", U64(0))
        limit = self.storage.get(f"session_call_limit_{session_key}", U64(0))
        if calls >= limit:
            return False

        return True

    # --- INTERNAL HELPERS ---

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_not_paused(self):
        if self.storage.get("paused", False):
            raise ContractError.PAUSED

    def _require_admin(self, caller: Address):
        if caller != self.storage.get("admin"):
            raise ContractError.UNAUTHORIZED

    def _get_now(self) -> U64:
        return self.env.ledger_timestamp()

    def _cleanup_session_state(self, session_key: Address):
        self.storage.remove(f"session_owner_{session_key}")
        self.storage.remove(f"session_expiry_{session_key}")
        self.storage.remove(f"session_call_limit_{session_key}")
        self.storage.remove(f"session_calls_made_{session_key}")
        self.storage.remove(f"session_rate_limit_{session_key}")
        self.storage.remove(f"session_window_start_{session_key}")
        self.storage.remove(f"session_window_calls_{session_key}")

        scopes_len = self.storage.get(f"scopes_len_{session_key}", U64(0))
        for i in range(int(scopes_len)):
            self.storage.remove(f"scope_contract_{session_key}_{i}")
            self.storage.remove(f"scope_method_{session_key}_{i}")
        self.storage.remove(f"scopes_len_{session_key}")

    def _require_allowed_scope(self, session_key: Address, target_contract: Address, method: Symbol):
        """Verifies if target contract and method are permitted in the registered scope list."""
        scopes_len = self.storage.get(f"scopes_len_{session_key}", U64(0))
        allowed = False

        for i in range(int(scopes_len)):
            c = self.storage.get(f"scope_contract_{session_key}_{i}")
            m = self.storage.get(f"scope_method_{session_key}_{i}")
            if c == target_contract and m == method:
                allowed = True
                break

        if not allowed:
            raise ContractError.SCOPE_NOT_ALLOWED

    def _apply_rate_limits(self, session_key: Address, now: U64):
        """Enforces rate limiting based on a rolling 60-second window."""
        window_start = self.storage.get(f"session_window_start_{session_key}", U64(0))
        window_calls = self.storage.get(f"session_window_calls_{session_key}", U64(0))
        rate_limit = self.storage.get(f"session_rate_limit_{session_key}", U64(0))

        if now < window_start + U64(60):
            # Same window
            if window_calls >= rate_limit:
                raise ContractError.RATE_LIMIT_EXCEEDED
            self.storage.set(f"session_window_calls_{session_key}", window_calls + U64(1))
        else:
            # Reset window
            self.storage.set(f"session_window_start_{session_key}", now)
            self.storage.set(f"session_window_calls_{session_key}", U64(1))
