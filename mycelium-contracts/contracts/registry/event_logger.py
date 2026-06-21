"""
EventLogger — Event indexing registry, query permissions, category tags.

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
    LOG_NOT_FOUND = 4
    ACCESS_DENIED = 5
    INVALID_TAG = 6

@contract
class EventLogger:
    """
    On-chain registry for indexing events and managing data access permissions.
    
    Allows contracts to record system logs tagged with categories.
    Restricts read access to specific tags based on viewer whitelist policies.
    """
    
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """
        Initializes the event logger contract.
        
        Args:
            admin: Admin address controlling authorization mapping.
        """
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED
            
        self.storage.set("admin", admin)
        self.storage.set("log_count", U64(0))
        self.storage.set("initialized", True)
        
        self.env.emit_event("initialized", {"admin": admin})

    @external
    def set_logger_authorization(self, caller: Address, logger: Address, authorized: Bool) -> Bool:
        """
        Authorizes or deauthorizes a contract/address to log events.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        self.storage.set("auth_log:" + str(logger), authorized)
        self.env.emit_event("logger_auth_updated", {"logger": logger, "authorized": authorized})
        return True

    @external
    def set_viewer_permission(
        self, 
        caller: Address, 
        viewer: Address, 
        tag: Symbol, 
        permitted: Bool
    ) -> Bool:
        """
        Grants or revokes a user access permission to query logs of a specific tag.
        
        Args:
            caller: Admin address.
            viewer: User address seeking query capabilities.
            tag: Target log category symbol.
            permitted: True to whitelist, False to remove.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        view_key = "view_perm:" + str(tag) + ":" + str(viewer)
        self.storage.set(view_key, permitted)
        
        self.env.emit_event(
            "viewer_permission_updated", 
            {"viewer": viewer, "tag": tag, "permitted": permitted}
        )
        return True

    @external
    def log_event(self, caller: Address, event_name: Symbol, tag: Symbol, payload: Bytes) -> U64:
        """
        Records an event entry in the log registry.
        
        Args:
            caller: The calling smart contract or system dispatcher (must be authorized).
            event_name: Description of the logged action.
            tag: Category tag (e.g. Symbol("TREASURY")).
            payload: Dynamic payload containing parameters or serialized hashes.
        """
        caller.require_auth()
        self._require_initialized()
        
        # Verify caller authorization
        if not self.storage.get("auth_log:" + str(caller), False):
            raise ContractError.UNAUTHORIZED
            
        if len(str(tag)) == 0:
            raise ContractError.INVALID_TAG
            
        log_id = self.storage.get("log_count", U64(0))
        timestamp = self.env.ledger().timestamp()
        
        # Store log attributes
        log_key = "log:" + str(log_id)
        self.storage.set(log_key + ":emitter", caller)
        self.storage.set(log_key + ":name", event_name)
        self.storage.set(log_key + ":tag", tag)
        self.storage.set(log_key + ":payload", payload)
        self.storage.set(log_key + ":time", timestamp)
        
        # Increment index
        self.storage.set("log_count", log_id + U64(1))
        
        # Emit blockchain event
        self.env.emit_event(
            "event_logged", 
            {"log_id": log_id, "emitter": caller, "name": event_name, "tag": tag}
        )
        return log_id

    @view
    def get_log(self, caller: Address, log_id: U64) -> Map:
        """
        Retrieves a logged event's full details.
        
        Enforces viewer permissions for the tag associated with the target log.
        
        Args:
            caller: User requesting the log record.
            log_id: Index of the log record.
        """
        caller.require_auth()
        self._require_initialized()
        
        count = self.storage.get("log_count", U64(0))
        if log_id >= count:
            raise ContractError.LOG_NOT_FOUND
            
        log_key = "log:" + str(log_id)
        tag = self.storage.get(log_key + ":tag")
        
        # Verify read access
        self._require_view_permission(caller, tag)
        
        log_details = Map()
        log_details.set(Symbol("id"), log_id)
        log_details.set(Symbol("emitter"), self.storage.get(log_key + ":emitter"))
        log_details.set(Symbol("name"), self.storage.get(log_key + ":name"))
        log_details.set(Symbol("tag"), tag)
        log_details.set(Symbol("payload"), self.storage.get(log_key + ":payload"))
        log_details.set(Symbol("timestamp"), self.storage.get(log_key + ":time"))
        return log_details

    @view
    def get_log_count(self) -> U64:
        """
        Returns the total count of logged events.
        """
        self._require_initialized()
        return self.storage.get("log_count", U64(0))

    @view
    def is_logger_authorized(self, logger: Address) -> Bool:
        """
        Checks if a given address is registered as an authorized emitter.
        """
        self._require_initialized()
        return self.storage.get("auth_log:" + str(logger), False)

    # Internal helpers
    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_view_permission(self, caller: Address, tag: Symbol):
        """
        Admin bypasses viewer permissions. Others must have view permissions.
        """
        admin = self.storage.get("admin")
        if caller == admin:
            return
            
        view_key = "view_perm:" + str(tag) + ":" + str(caller)
        if not self.storage.get(view_key, False):
            raise ContractError.ACCESS_DENIED
