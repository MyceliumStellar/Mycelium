"""
Decentralized Identity (DID) Registry — DID Document and Controller management.

Mycelium Smart Contract for Stellar. Maintains a registry of DID documents, maps controllers,
supports cryptographic verification key rotation and revocation, registers service endpoints,
and tracks version history.
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
    DID_ALREADY_EXISTS = 5
    DID_NOT_FOUND = 6
    KEY_NOT_FOUND = 7
    CONTROLLER_NOT_FOUND = 8
    SERVICE_NOT_FOUND = 9
    KEY_ALREADY_REVOKED = 10
    LAST_CONTROLLER_ERROR = 11

@contract
class DecentralizedIdentity:
    """
    Stellar Mycelium contract for decentralized identifier (DID) records.
    Provides methods to rotate/revoke keys, modify controllers, add service endpoints,
    and query the full DID document in an ERC725 / W3C compliant manner.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """Initialize the DID registry."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin})

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause registry updates."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- DID DOCUMENT CREATION ---

    @external
    def create_did(self, caller: Address, did: Address, controllers: Vec):
        """
        Register a new DID document for a subject address.
        
        Args:
            caller: Creator of the DID document.
            did: The subject Address of the DID (did:stellar:did).
            controllers: Addresses that are authorized to modify this DID.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if self.storage.get(f"did_active_{did}", False):
            raise ContractError.DID_ALREADY_EXISTS

        # Ensure we have at least one controller
        if len(controllers) == 0:
            raise ContractError.LAST_CONTROLLER_ERROR

        self.storage.set(f"did_active_{did}", True)
        self.storage.set(f"did_version_{did}", U64(1))

        # Register controllers
        self.storage.set(f"controllers_count_{did}", len(controllers))
        for i in range(len(controllers)):
            self.storage.set(f"controller_{did}_{i}", controllers.get(i))

        # Initialize empty collections for keys and services
        self.storage.set(f"keys_count_{did}", U64(0))
        self.storage.set(f"services_count_{did}", U64(0))

        self.env.emit_event("did_created", {
            "did": did,
            "creator": caller,
            "version": U64(1)
        })

    # --- KEY MANAGEMENT (ROTATION & REVOCATION) ---

    @external
    def add_verification_key(
        self,
        caller: Address,
        did: Address,
        key_id: Bytes,
        key_type: Symbol,
        public_key: Bytes
    ):
        """Add a verification public key to the DID Document."""
        caller.require_auth()
        self._require_initialized()
        self._require_controller(did, caller)

        count = self.storage.get(f"keys_count_{did}", U64(0))
        
        self.storage.set(f"key_id_{did}_{count}", key_id)
        self.storage.set(f"key_type_{did}_{count}", key_type)
        self.storage.set(f"key_pub_{did}_{count}", public_key)
        self.storage.set(f"key_revoked_{did}_{count}", False)
        
        self.storage.set(f"keys_count_{did}", count + U64(1))
        self._increment_version(did)

        self.env.emit_event("key_added", {
            "did": did,
            "key_id": key_id,
            "key_type": key_type
        })

    @external
    def rotate_verification_key(self, caller: Address, did: Address, key_id: Bytes, new_public_key: Bytes):
        """Rotate (replace) an existing verification key."""
        caller.require_auth()
        self._require_initialized()
        self._require_controller(did, caller)

        index = self._find_key_index(did, key_id)
        
        # Check if already revoked
        if self.storage.get(f"key_revoked_{did}_{index}", False):
            raise ContractError.KEY_ALREADY_REVOKED

        # Rotate key
        self.storage.set(f"key_pub_{did}_{index}", new_public_key)
        self._increment_version(did)

        self.env.emit_event("key_rotated", {
            "did": did,
            "key_id": key_id
        })

    @external
    def revoke_verification_key(self, caller: Address, did: Address, key_id: Bytes):
        """Revoke a verification key permanently."""
        caller.require_auth()
        self._require_initialized()
        self._require_controller(did, caller)

        index = self._find_key_index(did, key_id)
        
        if self.storage.get(f"key_revoked_{did}_{index}", False):
            raise ContractError.KEY_ALREADY_REVOKED

        self.storage.set(f"key_revoked_{did}_{index}", True)
        self._increment_version(did)

        self.env.emit_event("key_revoked", {
            "did": did,
            "key_id": key_id
        })

    # --- CONTROLLER MANAGEMENT ---

    @external
    def add_controller(self, caller: Address, did: Address, new_controller: Address):
        """Add a new controller authorized to edit this DID Document."""
        caller.require_auth()
        self._require_initialized()
        self._require_controller(did, caller)

        count = self.storage.get(f"controllers_count_{did}", U64(0))
        
        # Check if already exists
        for i in range(int(count)):
            if self.storage.get(f"controller_{did}_{i}") == new_controller:
                return # Already exists

        self.storage.set(f"controller_{did}_{count}", new_controller)
        self.storage.set(f"controllers_count_{did}", count + U64(1))
        self._increment_version(did)

        self.env.emit_event("controller_added", {
            "did": did,
            "new_controller": new_controller
        })

    @external
    def remove_controller(self, caller: Address, did: Address, controller_to_remove: Address):
        """Remove an authorized controller."""
        caller.require_auth()
        self._require_initialized()
        self._require_controller(did, caller)

        count = self.storage.get(f"controllers_count_{did}", U64(0))
        if count <= U64(1):
            raise ContractError.LAST_CONTROLLER_ERROR

        found = False
        for i in range(int(count)):
            if self.storage.get(f"controller_{did}_{i}") == controller_to_remove:
                # Replace with the last controller in list
                last_idx = count - U64(1)
                last_controller = self.storage.get(f"controller_{did}_{last_idx}")
                self.storage.set(f"controller_{did}_{i}", last_controller)
                
                self.storage.remove(f"controller_{did}_{last_idx}")
                self.storage.set(f"controllers_count_{did}", last_idx)
                
                found = True
                break

        if not found:
            raise ContractError.CONTROLLER_NOT_FOUND

        self._increment_version(did)
        self.env.emit_event("controller_removed", {
            "did": did,
            "removed_controller": controller_to_remove
        })

    # --- SERVICE ENDPOINTS ---

    @external
    def add_service(self, caller: Address, did: Address, service_id: Bytes, service_type: Symbol, service_url: Bytes):
        """Add service endpoint metadata to the DID Document."""
        caller.require_auth()
        self._require_initialized()
        self._require_controller(did, caller)

        count = self.storage.get(f"services_count_{did}", U64(0))

        # Check uniqueness of service_id
        for i in range(int(count)):
            if self.storage.get(f"service_id_{did}_{i}") == service_id:
                # Overwrite existing
                self.storage.set(f"service_type_{did}_{i}", service_type)
                self.storage.set(f"service_url_{did}_{i}", service_url)
                self._increment_version(did)
                return

        self.storage.set(f"service_id_{did}_{count}", service_id)
        self.storage.set(f"service_type_{did}_{count}", service_type)
        self.storage.set(f"service_url_{did}_{count}", service_url)
        self.storage.set(f"services_count_{did}", count + U64(1))
        
        self._increment_version(did)
        self.env.emit_event("service_added", {
            "did": did,
            "service_id": service_id,
            "service_type": service_type
        })

    @external
    def remove_service(self, caller: Address, did: Address, service_id: Bytes):
        """Remove a service endpoint from the DID document."""
        caller.require_auth()
        self._require_initialized()
        self._require_controller(did, caller)

        count = self.storage.get(f"services_count_{did}", U64(0))
        found = False

        for i in range(int(count)):
            if self.storage.get(f"service_id_{did}_{i}") == service_id:
                last_idx = count - U64(1)
                
                # Replace with last element
                self.storage.set(f"service_id_{did}_{i}", self.storage.get(f"service_id_{did}_{last_idx}"))
                self.storage.set(f"service_type_{did}_{i}", self.storage.get(f"service_type_{did}_{last_idx}"))
                self.storage.set(f"service_url_{did}_{i}", self.storage.get(f"service_url_{did}_{last_idx}"))

                # Clear last element
                self.storage.remove(f"service_id_{did}_{last_idx}")
                self.storage.remove(f"service_type_{did}_{last_idx}")
                self.storage.remove(f"service_url_{did}_{last_idx}")
                self.storage.set(f"services_count_{did}", last_idx)
                
                found = True
                break

        if not found:
            raise ContractError.SERVICE_NOT_FOUND

        self._increment_version(did)
        self.env.emit_event("service_removed", {"did": did, "service_id": service_id})

    # --- VIEWS ---

    @view
    def get_did_document(self, did: Address) -> Map:
        """Returns the compiled DID document representation including version, controllers, keys, and services."""
        self._require_initialized()
        if not self.storage.get(f"did_active_{did}", False):
            raise ContractError.DID_NOT_FOUND

        doc = Map(self.env)
        doc.set("id", Bytes(f"did:stellar:{did}".encode("utf-8")))
        doc.set("version", self.storage.get(f"did_version_{did}", U64(0)))

        # Build controllers vector
        controllers_list = Vec(self.env)
        ctrl_count = self.storage.get(f"controllers_count_{did}", U64(0))
        for i in range(int(ctrl_count)):
            controllers_list.push_back(self.storage.get(f"controller_{did}_{i}"))
        doc.set("controllers", controllers_list)

        # Build keys list
        keys_list = Vec(self.env)
        key_count = self.storage.get(f"keys_count_{did}", U64(0))
        for i in range(int(key_count)):
            k = Map(self.env)
            k.set("id", self.storage.get(f"key_id_{did}_{i}"))
            k.set("type", self.storage.get(f"key_type_{did}_{i}"))
            k.set("publicKey", self.storage.get(f"key_pub_{did}_{i}"))
            k.set("revoked", self.storage.get(f"key_revoked_{did}_{i}"))
            keys_list.push_back(k)
        doc.set("verificationMethods", keys_list)

        # Build services list
        services_list = Vec(self.env)
        svc_count = self.storage.get(f"services_count_{did}", U64(0))
        for i in range(int(svc_count)):
            s = Map(self.env)
            s.set("id", self.storage.get(f"service_id_{did}_{i}"))
            s.set("type", self.storage.get(f"service_type_{did}_{i}"))
            s.set("serviceEndpoint", self.storage.get(f"service_url_{did}_{i}"))
            services_list.push_back(s)
        doc.set("services", services_list)

        return doc

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

    def _require_controller(self, did: Address, caller: Address):
        if not self.storage.get(f"did_active_{did}", False):
            raise ContractError.DID_NOT_FOUND

        count = self.storage.get(f"controllers_count_{did}", U64(0))
        found = False
        for i in range(int(count)):
            if self.storage.get(f"controller_{did}_{i}") == caller:
                found = True
                break
        if not found:
            raise ContractError.UNAUTHORIZED

    def _increment_version(self, did: Address):
        v = self.storage.get(f"did_version_{did}", U64(0))
        self.storage.set(f"did_version_{did}", v + U64(1))

    def _find_key_index(self, did: Address, key_id: Bytes) -> U64:
        count = self.storage.get(f"keys_count_{did}", U64(0))
        for i in range(int(count)):
            if self.storage.get(f"key_id_{did}_{i}") == key_id:
                return U64(i)
        raise ContractError.KEY_NOT_FOUND
