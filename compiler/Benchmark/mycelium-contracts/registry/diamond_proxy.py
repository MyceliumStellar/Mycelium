"""
DiamondProxy — Diamond standard multi-facet selector routing, function registry, upgrade overrides.

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
    SELECTOR_NOT_FOUND = 4
    SELECTOR_LOCKED = 5
    FACET_ALREADY_EXISTS = 6
    FACET_NOT_FOUND = 7
    SELECTOR_CLASH = 8
    INVALID_SELECTION = 9

@contract
class DiamondProxy:
    """
    Diamond Proxy Routing contract.
    
    Acts as a single entry point that delegates function calls to multiple logic contracts
    (facets) based on the Symbol selector.
    """
    
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """
        Initializes the Diamond Proxy contract.
        
        Args:
            admin: Admin address controlling facet registration.
        """
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED
            
        self.storage.set("admin", admin)
        self.storage.set("initialized", True)
        
        self.env.emit_event("initialized", {"admin": admin})

    @external
    def add_facet(self, caller: Address, facet: Address, selectors: Vec) -> Bool:
        """
        Registers a new facet with associated function selectors.
        
        Args:
            caller: Admin address.
            facet: Target implementation address.
            selectors: Vec of Symbol selectors mapped to this facet.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        # Verify facet not already registered
        f_reg_key = "f_reg:" + str(facet)
        if self.storage.get(f_reg_key, False):
            raise ContractError.FACET_ALREADY_EXISTS
            
        # Store registration
        self.storage.set(f_reg_key, True)
        
        # Bind each selector
        i = 0
        while i < len(selectors):
            selector = selectors[i]
            sel_key = "sel:" + str(selector)
            
            # Check for selector clashes
            if self.storage.has(sel_key):
                raise ContractError.SELECTOR_CLASH
                
            self.storage.set(sel_key, facet)
            i += 1
            
        # Store selectors list under facet
        self.storage.set("f_sels:" + str(facet), selectors)
        
        self.env.emit_event("facet_added", {"facet": facet, "selectors": selectors})
        return True

    @external
    def remove_facet(self, caller: Address, facet: Address) -> Bool:
        """
        Removes a facet and unbinds all associated function selectors.
        
        Args:
            caller: Admin address.
            facet: Target implementation address to remove.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        f_reg_key = "f_reg:" + str(facet)
        if not self.storage.get(f_reg_key, False):
            raise ContractError.FACET_NOT_FOUND
            
        selectors = self.storage.get("f_sels:" + str(facet))
        
        # Unbind selectors
        i = 0
        while i < len(selectors):
            selector = selectors[i]
            sel_key = "sel:" + str(selector)
            
            # Check upgrade override locks
            if self.storage.get("lock:" + str(selector), False):
                raise ContractError.SELECTOR_LOCKED
                
            self.storage.remove(sel_key)
            i += 1
            
        self.storage.remove(f_reg_key)
        self.storage.remove("f_sels:" + str(facet))
        
        self.env.emit_event("facet_removed", {"facet": facet})
        return True

    @external
    def replace_facet(
        self, 
        caller: Address, 
        old_facet: Address, 
        new_facet: Address, 
        selectors: Vec
    ) -> Bool:
        """
        Replaces selectors from an old facet with a new facet.
        
        Args:
            caller: Admin address.
            old_facet: Facet currently managing these selectors.
            new_facet: New facet addressing these selectors.
            selectors: Vec of Symbol selectors to migrate.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        if not self.storage.get("f_reg:" + str(old_facet), False):
            raise ContractError.FACET_NOT_FOUND
            
        # Ensure new facet registered or register it
        new_f_reg = "f_reg:" + str(new_facet)
        if not self.storage.get(new_f_reg, False):
            self.storage.set(new_f_reg, True)
            self.storage.set("f_sels:" + str(new_facet), Vec())
            
        # Update selectors
        i = 0
        while i < len(selectors):
            selector = selectors[i]
            sel_key = "sel:" + str(selector)
            
            # Verify selector was registered to old facet
            if self.storage.get(sel_key) != old_facet:
                raise ContractError.INVALID_SELECTION
                
            # Verify override lock status
            if self.storage.get("lock:" + str(selector), False):
                raise ContractError.SELECTOR_LOCKED
                
            self.storage.set(sel_key, new_facet)
            i += 1
            
        self.env.emit_event(
            "facet_replaced", 
            {"old_facet": old_facet, "new_facet": new_facet, "selectors": selectors}
        )
        return True

    @external
    def lock_selector(self, caller: Address, selector: Symbol) -> Bool:
        """
        Permanently locks upgrade options for a specific selector.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        sel_key = "sel:" + str(selector)
        if not self.storage.has(sel_key):
            raise ContractError.SELECTOR_NOT_FOUND
            
        self.storage.set("lock:" + str(selector), True)
        self.env.emit_event("selector_locked", {"selector": selector})
        return True

    @external
    def execute_call(self, selector: Symbol, args: Vec) -> Vec:
        """
        Routes the function call to the appropriate facet using selectors.
        """
        self._require_initialized()
        
        sel_key = "sel:" + str(selector)
        if not self.storage.has(sel_key):
            raise ContractError.SELECTOR_NOT_FOUND
            
        facet = self.storage.get(sel_key)
        
        # Invoke facet
        result = self.env.invoke_contract(facet, selector, args)
        return result

    @view
    def get_facet_by_selector(self, selector: Symbol) -> Address:
        """
        Queries which facet address manages a selector.
        """
        self._require_initialized()
        sel_key = "sel:" + str(selector)
        if not self.storage.has(sel_key):
            raise ContractError.SELECTOR_NOT_FOUND
        return self.storage.get(sel_key)

    @view
    def get_facet_selectors(self, facet: Address) -> Vec:
        """
        Returns all registered selectors for a given facet.
        """
        self._require_initialized()
        f_reg_key = "f_reg:" + str(facet)
        if not self.storage.get(f_reg_key, False):
            raise ContractError.FACET_NOT_FOUND
        return self.storage.get("f_sels:" + str(facet))

    @view
    def is_selector_locked(self, selector: Symbol) -> Bool:
        """
        Checks if a selector has upgrade locks enabled.
        """
        self._require_initialized()
        return self.storage.get("lock:" + str(selector), False)

    # Internal helpers
    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED
