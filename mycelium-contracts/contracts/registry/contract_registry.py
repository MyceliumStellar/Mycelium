"""
ContractRegistry — Address mapping versioning, deprecation timestamps, proxy routing maps.

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
    VERSION_ALREADY_EXISTS = 4
    CONTRACT_NOT_FOUND = 5
    CONTRACT_DEPRECATED = 6
    TIMELOCK_NOT_EXPIRED = 7
    PROPOSAL_NOT_FOUND = 8
    INVALID_VERSION = 9

@contract
class ContractRegistry:
    """
    Registry managing smart contract addresses, versions, and upgrade lifecycles.
    
    Provides a dynamic resolver mechanism that routing proxies can query.
    Enforces a proposal timelock delay for implementation upgrades.
    """
    
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()
        
    @external
    def initialize(self, admin: Address, timelock_delay: U64):
        """
        Initializes the contract registry with administration configurations.
        
        Args:
            admin: Address with administrative credentials.
            timelock_delay: Delay duration in seconds before upgrade proposal execution.
        """
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED
            
        self.storage.set("admin", admin)
        self.storage.set("timelock_delay", timelock_delay)
        self.storage.set("initialized", True)
        
        self.env.emit_event("initialized", {"admin": admin, "timelock_delay": timelock_delay})

    @external
    def register_contract(
        self, 
        caller: Address, 
        name: Symbol, 
        contract_address: Address, 
        version: U64, 
        description: Bytes
    ) -> Bool:
        """
        Registers a specific contract version and maps it to the logical name.
        
        Args:
            caller: Admin address authorizing this registration.
            name: Symbol identifier for the contract module (e.g. Symbol("Lending")).
            contract_address: The deployed address of the contract.
            version: Integer representation of the contract version (must be incrementing).
            description: Short text summary or hash of the release notes.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        # Check if version exists
        v_key = "v_addr:" + str(name) + ":" + str(version)
        if self.storage.has(v_key):
            raise ContractError.VERSION_ALREADY_EXISTS
            
        active_ver = self.storage.get("act_ver:" + str(name), U64(0))
        if version <= active_ver and active_ver != U64(0):
            raise ContractError.INVALID_VERSION
            
        # Store metadata
        self.storage.set(v_key, contract_address)
        self.storage.set("v_desc:" + str(name) + ":" + str(version), description)
        self.storage.set("v_time:" + str(name) + ":" + str(version), self.env.ledger().timestamp())
        self.storage.set("v_depr:" + str(name) + ":" + str(version), U64(0)) # 0 means not deprecated
        
        # Update current active pointer
        self.storage.set("act_addr:" + str(name), contract_address)
        self.storage.set("act_ver:" + str(name), version)
        
        self.env.emit_event(
            "contract_registered", 
            {"name": name, "address": contract_address, "version": version}
        )
        return True

    @external
    def deprecate_version(
        self, 
        caller: Address, 
        name: Symbol, 
        version: U64, 
        deprecation_time: U64
    ) -> Bool:
        """
        Schedules deprecation timestamp for a registered contract version.
        
        Args:
            caller: Admin address.
            name: Contract module name.
            version: Target version.
            deprecation_time: UNIX timestamp when the version becomes invalid.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        v_key = "v_addr:" + str(name) + ":" + str(version)
        if not self.storage.has(v_key):
            raise ContractError.CONTRACT_NOT_FOUND
            
        self.storage.set("v_depr:" + str(name) + ":" + str(version), deprecation_time)
        
        self.env.emit_event(
            "contract_deprecated", 
            {"name": name, "version": version, "deprecation_time": deprecation_time}
        )
        return True

    @external
    def propose_upgrade(
        self, 
        caller: Address, 
        name: Symbol, 
        proposed_address: Address, 
        next_version: U64
    ) -> Bool:
        """
        Proposes an implementation upgrade to initiate the timelock window.
        
        Args:
            caller: Admin address.
            name: Contract module name.
            proposed_address: New logic address to upgrade to.
            next_version: Version number of new implementation.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        active_ver = self.storage.get("act_ver:" + str(name), U64(0))
        if next_version <= active_ver:
            raise ContractError.INVALID_VERSION
            
        current_time = self.env.ledger().timestamp()
        delay = self.storage.get("timelock_delay", U64(0))
        executable_time = current_time + delay
        
        prop_key = "prop:" + str(name)
        self.storage.set(prop_key + ":addr", proposed_address)
        self.storage.set(prop_key + ":ver", next_version)
        self.storage.set(prop_key + ":exec", executable_time)
        
        self.env.emit_event(
            "upgrade_proposed", 
            {"name": name, "proposed_address": proposed_address, "version": next_version, "executable_time": executable_time}
        )
        return True

    @external
    def execute_upgrade(self, caller: Address, name: Symbol, description: Bytes) -> Bool:
        """
        Executes a proposed upgrade after timelock expiration.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        prop_key = "prop:" + str(name)
        if not self.storage.has(prop_key + ":addr"):
            raise ContractError.PROPOSAL_NOT_FOUND
            
        exec_time = self.storage.get(prop_key + ":exec", U64(0))
        current_time = self.env.ledger().timestamp()
        if current_time < exec_time:
            raise ContractError.TIMELOCK_NOT_EXPIRED
            
        new_addr = self.storage.get(prop_key + ":addr")
        new_ver = self.storage.get(prop_key + ":ver")
        
        # Perform registration
        v_key = "v_addr:" + str(name) + ":" + str(new_ver)
        self.storage.set(v_key, new_addr)
        self.storage.set("v_desc:" + str(name) + ":" + str(new_ver), description)
        self.storage.set("v_time:" + str(name) + ":" + str(new_ver), current_time)
        self.storage.set("v_depr:" + str(name) + ":" + str(new_ver), U64(0))
        
        # Update active pointer
        self.storage.set("act_addr:" + str(name), new_addr)
        self.storage.set("act_ver:" + str(name), new_ver)
        
        # Delete proposal keys
        self.storage.remove(prop_key + ":addr")
        self.storage.remove(prop_key + ":ver")
        self.storage.remove(prop_key + ":exec")
        
        self.env.emit_event(
            "upgrade_executed", 
            {"name": name, "address": new_addr, "version": new_ver}
        )
        return True

    @view
    def resolve_contract(self, name: Symbol) -> Address:
        """
        Resolves the current active address of a contract logical name.
        
        Verifies that the resolved contract version is not currently deprecated.
        """
        self._require_initialized()
        
        addr_key = "act_addr:" + str(name)
        if not self.storage.has(addr_key):
            raise ContractError.CONTRACT_NOT_FOUND
            
        version = self.storage.get("act_ver:" + str(name))
        depr_time = self.storage.get("v_depr:" + str(name) + ":" + str(version), U64(0))
        
        if depr_time != U64(0) and self.env.ledger().timestamp() >= depr_time:
            raise ContractError.CONTRACT_DEPRECATED
            
        return self.storage.get(addr_key)

    @view
    def get_version_details(self, name: Symbol, version: U64) -> Map:
        """
        Retrieves registration and deprecation details for a given contract version.
        """
        self._require_initialized()
        v_key = "v_addr:" + str(name) + ":" + str(version)
        if not self.storage.has(v_key):
            raise ContractError.CONTRACT_NOT_FOUND
            
        details = Map()
        details.set(Symbol("address"), self.storage.get(v_key))
        details.set(Symbol("description"), self.storage.get("v_desc:" + str(name) + ":" + str(version)))
        details.set(Symbol("registered_at"), self.storage.get("v_time:" + str(name) + ":" + str(version)))
        details.set(Symbol("deprecated_at"), self.storage.get("v_depr:" + str(name) + ":" + str(version)))
        return details

    @view
    def get_proposal_details(self, name: Symbol) -> Map:
        """
        Gets details of any pending upgrade proposals for a module name.
        """
        self._require_initialized()
        prop_key = "prop:" + str(name)
        if not self.storage.has(prop_key + ":addr"):
            raise ContractError.PROPOSAL_NOT_FOUND
            
        details = Map()
        details.set(Symbol("proposed_address"), self.storage.get(prop_key + ":addr"))
        details.set(Symbol("version"), self.storage.get(prop_key + ":ver"))
        details.set(Symbol("executable_time"), self.storage.get(prop_key + ":exec"))
        return details

    # Internal helper methods
    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED
