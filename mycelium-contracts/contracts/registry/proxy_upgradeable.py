"""
ProxyUpgradeable — UUPS proxy routing, upgrade rules, administrative limits, storage collision checks.

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
    UPGRADE_LOCKED = 4
    COLLISION_DETECTED = 5
    INVALID_IMPLEMENTATION = 6
    COOLDOWN_ACTIVE = 7
    COMPATIBILITY_CHECK_FAILED = 8

@contract
class ProxyUpgradeable:
    """
    UUPS-compliant proxy routing contract.
    
    Routes arbitrary calls to a logic contract using `env.invoke_contract`.
    Manages logic implementation versioning, administrative cooldowns,
    and storage collision schema hash validation.
    """
    
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, initial_impl: Address, initial_schema: Bytes):
        """
        Initializes the upgradeable proxy configuration.
        
        Args:
            admin: Admin address controlling upgrades.
            initial_impl: Deployment address of the initial implementation.
            initial_schema: Schema layout hash of the initial implementation to prevent collisions.
        """
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED
            
        self.storage.set("admin", admin)
        self.storage.set("implementation", initial_impl)
        self.storage.set("schema_hash", initial_schema)
        self.storage.set("initialized", True)
        self.storage.set("upgrade_locked", False)
        self.storage.set("last_upgrade_time", self.env.ledger().timestamp())
        self.storage.set("upgrade_cooldown", U64(86400)) # 24-hour cooldown by default
        
        self.env.emit_event(
            "initialized", 
            {"admin": admin, "implementation": initial_impl, "schema_hash": initial_schema}
        )

    @external
    def upgrade_to(self, caller: Address, new_impl: Address, new_schema: Bytes) -> Bool:
        """
        Upgrades the proxy to point to a new logic contract address.
        
        Enforces cooldown windows, administrative approval, collision prevention validation,
        and lock states.
        
        Args:
            caller: Admin address authorizing this upgrade.
            new_impl: Address of the new logic contract.
            new_schema: Storage schema hash of the new logic contract.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self._require_upgrades_active()
        
        if new_impl == self.storage.get("implementation"):
            raise ContractError.INVALID_IMPLEMENTATION
            
        # Verify Cooldown
        current_time = self.env.ledger().timestamp()
        last_upgrade = self.storage.get("last_upgrade_time", U64(0))
        cooldown = self.storage.get("upgrade_cooldown", U64(0))
        if current_time < last_upgrade + cooldown:
            raise ContractError.COOLDOWN_ACTIVE
            
        # Validate schema to prevent storage collisions
        old_schema = self.storage.get("schema_hash")
        if not self._check_schema_compatibility(old_schema, new_schema):
            raise ContractError.COLLISION_DETECTED
            
        # Apply upgrade
        self.storage.set("implementation", new_impl)
        self.storage.set("schema_hash", new_schema)
        self.storage.set("last_upgrade_time", current_time)
        
        self.env.emit_event(
            "upgraded", 
            {"old_impl": self.storage.get("implementation"), "new_impl": new_impl, "schema_hash": new_schema}
        )
        return True

    @external
    def lock_upgrades_permanently(self, caller: Address) -> Bool:
        """
        Irrevocably locks proxy upgrades, making the contract immutable.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        self.storage.set("upgrade_locked", True)
        self.env.emit_event("upgrades_locked", {})
        return True

    @external
    def set_cooldown(self, caller: Address, new_cooldown: U64) -> Bool:
        """
        Modifies the upgrade cooldown window. Max cooldown of 30 days.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        # Max cooldown limit of 30 days (2,592,000 seconds)
        if new_cooldown > U64(2592000):
            raise ContractError.COMPATIBILITY_CHECK_FAILED
            
        self.storage.set("upgrade_cooldown", new_cooldown)
        self.env.emit_event("cooldown_updated", {"cooldown": new_cooldown})
        return True

    @external
    def execute_route(self, method: Symbol, args: Vec) -> Vec:
        """
        Routes the execution to the implementation contract.
        
        Simulates dynamic routing by calling `env.invoke_contract` on the implementation.
        """
        self._require_initialized()
        impl = self.storage.get("implementation")
        
        # Invoke implementation
        result = self.env.invoke_contract(impl, method, args)
        return result

    @view
    def get_implementation(self) -> Address:
        """
        Returns the current implementation contract address.
        """
        self._require_initialized()
        return self.storage.get("implementation")

    @view
    def get_schema_hash(self) -> Bytes:
        """
        Returns the current stored storage schema hash.
        """
        self._require_initialized()
        return self.storage.get("schema_hash")

    @view
    def is_upgrade_locked(self) -> Bool:
        """
        Indicates if upgrades have been permanently disabled.
        """
        self._require_initialized()
        return self.storage.get("upgrade_locked", False)

    # Internal helpers
    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_upgrades_active(self):
        if self.storage.get("upgrade_locked", False):
            raise ContractError.UPGRADE_LOCKED

    def _check_schema_compatibility(self, old_schema: Bytes, new_schema: Bytes) -> Bool:
        """
        Verifies if the new schema is compatible.
        
        For demonstration, requires that the new schema contains the old schema prefix 
        or meets compatibility criteria. In production this would do bitwise or hash checks.
        """
        if len(new_schema) < len(old_schema):
            return False
            
        # Basic byte-level prefix comparison to verify backward compatibility
        i = 0
        while i < len(old_schema):
            if old_schema[i] != new_schema[i]:
                return False
            i += 1
            
        return True
