"""
FactoryContract — Template registry, clone creations, initialization parameters, count maps.

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
    TEMPLATE_ALREADY_EXISTS = 4
    TEMPLATE_NOT_FOUND = 5
    TEMPLATE_DEPRECATED = 6
    CLONE_FAILED = 7
    INVALID_SALT = 8

@contract
class FactoryContract:
    """
    Factory registry for deploying and tracking clone contract instances.
    
    Maintains a list of active WASM templates, deploys instances dynamically
    via Stellar deployer APIs, and forwards initialization parameters.
    """
    
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """
        Initializes the factory contract with administration.
        
        Args:
            admin: Admin address controlling template registration.
        """
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED
            
        self.storage.set("admin", admin)
        self.storage.set("initialized", True)
        
        self.env.emit_event("initialized", {"admin": admin})

    @external
    def register_template(self, caller: Address, template_id: Symbol, wasm_hash: Bytes) -> Bool:
        """
        Registers a new WASM template configuration.
        
        Args:
            caller: Admin address.
            template_id: Unique symbol naming the template (e.g. Symbol("TokenTemplate")).
            wasm_hash: 32-byte hash identifying the uploaded WASM bytecode.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        t_key = "t_hash:" + str(template_id)
        if self.storage.has(t_key):
            raise ContractError.TEMPLATE_ALREADY_EXISTS
            
        self.storage.set(t_key, wasm_hash)
        self.storage.set("t_active:" + str(template_id), True)
        self.storage.set("t_count:" + str(template_id), U64(0))
        
        self.env.emit_event("template_registered", {"id": template_id, "hash": wasm_hash})
        return True

    @external
    def deprecate_template(self, caller: Address, template_id: Symbol) -> Bool:
        """
        Deprecates an existing template, preventing further deployments.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        t_key = "t_hash:" + str(template_id)
        if not self.storage.has(t_key):
            raise ContractError.TEMPLATE_NOT_FOUND
            
        self.storage.set("t_active:" + str(template_id), False)
        
        self.env.emit_event("template_deprecated", {"id": template_id})
        return True

    @external
    def create_clone(
        self, 
        caller: Address, 
        template_id: Symbol, 
        salt: Bytes, 
        init_args: Vec
    ) -> Address:
        """
        Deploys a clone contract of a registered template and calls its initialize method.
        
        Args:
            caller: Instantiating account address.
            template_id: Identifier of the template to deploy.
            salt: Deterministic salt for Address computation.
            init_args: Vec of arguments passed to the clone's initialize method.
        """
        caller.require_auth()
        self._require_initialized()
        
        # Verify template
        t_key = "t_hash:" + str(template_id)
        if not self.storage.has(t_key):
            raise ContractError.TEMPLATE_NOT_FOUND
            
        if not self.storage.get("t_active:" + str(template_id), False):
            raise ContractError.TEMPLATE_DEPRECATED
            
        if len(salt) != 32:
            raise ContractError.INVALID_SALT
            
        wasm_hash = self.storage.get(t_key)
        
        # Deploy contract clone
        # In Soroban environment: self.env.deployer().with_current_contract(salt).deploy(wasm_hash)
        deployer = self.env.deployer().with_current_contract(salt)
        clone_address = deployer.deploy(wasm_hash)
        
        # Initialize clone contract
        self.env.invoke_contract(clone_address, Symbol("initialize"), init_args)
        
        # Record clone metadata
        count = self.storage.get("t_count:" + str(template_id), U64(0))
        self.storage.set("t_clone:" + str(template_id) + ":" + str(count), clone_address)
        self.storage.set("clone_owner:" + str(clone_address), caller)
        self.storage.set("t_count:" + str(template_id), count + U64(1))
        
        self.env.emit_event(
            "clone_created", 
            {
                "template_id": template_id, 
                "clone_address": clone_address, 
                "owner": caller, 
                "index": count
            }
        )
        return clone_address

    @view
    def get_clone_count(self, template_id: Symbol) -> U64:
        """
        Returns the total number of clones deployed for a template.
        """
        self._require_initialized()
        if not self.storage.has("t_hash:" + str(template_id)):
            raise ContractError.TEMPLATE_NOT_FOUND
        return self.storage.get("t_count:" + str(template_id), U64(0))

    @view
    def get_clone_address(self, template_id: Symbol, index: U64) -> Address:
        """
        Returns the address of a deployed clone at a specific template index.
        """
        self._require_initialized()
        count = self.storage.get("t_count:" + str(template_id), U64(0))
        if index >= count:
            raise ContractError.CLONE_FAILED
            
        return self.storage.get("t_clone:" + str(template_id) + ":" + str(index))

    @view
    def get_clone_owner(self, clone_address: Address) -> Address:
        """
        Returns the creator/owner of a deployed clone instance.
        """
        self._require_initialized()
        owner_key = "clone_owner:" + str(clone_address)
        if not self.storage.has(owner_key):
            raise ContractError.CLONE_FAILED
        return self.storage.get(owner_key)

    @view
    def is_template_active(self, template_id: Symbol) -> Bool:
        """
        Returns whether a template can be cloned.
        """
        self._require_initialized()
        if not self.storage.has("t_hash:" + str(template_id)):
            return False
        return self.storage.get("t_active:" + str(template_id), False)

    # Internal helpers
    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED
