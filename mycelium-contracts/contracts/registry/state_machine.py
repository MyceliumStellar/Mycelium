"""
StateMachine — State transition maps, guard validation callbacks, state timestamps.

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
    TRANSITION_NOT_ALLOWED = 4
    GUARD_VALIDATION_FAILED = 5
    ITEM_ALREADY_INITIALIZED = 6
    ITEM_NOT_INITIALIZED = 7
    INVALID_STATE = 8

@contract
class StateMachine:
    """
    Finite State Machine for managing workflow lifecycle state transitions.
    
    Permits admins to configure transition matrices.
    Enforces transitions via optional guard callback contracts and records historic timestamps.
    """
    
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """
        Initializes the state machine with admin controls.
        
        Args:
            admin: Admin address controlling states and guards configuration.
        """
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED
            
        self.storage.set("admin", admin)
        self.storage.set("initialized", True)
        
        self.env.emit_event("initialized", {"admin": admin})

    @external
    def define_transition(
        self, 
        caller: Address, 
        from_state: Symbol, 
        to_state: Symbol, 
        guard_contract: Address
    ) -> Bool:
        """
        Registers an allowed edge in the state transition matrix.
        
        Args:
            caller: Admin address.
            from_state: Origin state symbol.
            to_state: Destination state symbol.
            guard_contract: Address of a contract implementing `validate_transition`.
                           Pass admin address itself if no guard check is required.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        if len(str(from_state)) == 0 or len(str(to_state)) == 0:
            raise ContractError.INVALID_STATE
            
        trans_key = "trans:" + str(from_state) + ":" + str(to_state)
        self.storage.set(trans_key, True)
        self.storage.set("guard:" + str(from_state) + ":" + str(to_state), guard_contract)
        
        self.env.emit_event(
            "transition_defined", 
            {"from": from_state, "to": to_state, "guard": guard_contract}
        )
        return True

    @external
    def remove_transition(self, caller: Address, from_state: Symbol, to_state: Symbol) -> Bool:
        """
        Removes a transition edge from the matrix.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        trans_key = "trans:" + str(from_state) + ":" + str(to_state)
        if not self.storage.has(trans_key):
            raise ContractError.TRANSITION_NOT_ALLOWED
            
        self.storage.remove(trans_key)
        self.storage.remove("guard:" + str(from_state) + ":" + str(to_state))
        
        self.env.emit_event("transition_removed", {"from": from_state, "to": to_state})
        return True

    @external
    def initialize_item(self, caller: Address, item_id: Bytes, initial_state: Symbol) -> Bool:
        """
        Creates/initiates a state-tracked workflow item.
        """
        caller.require_auth()
        self._require_initialized()
        
        item_key = "item_st:" + str(item_id)
        if self.storage.has(item_key):
            raise ContractError.ITEM_ALREADY_INITIALIZED
            
        if len(str(initial_state)) == 0:
            raise ContractError.INVALID_STATE
            
        current_time = self.env.ledger().timestamp()
        
        self.storage.set(item_key, initial_state)
        self.storage.set("item_time:" + str(item_id) + ":" + str(initial_state), current_time)
        
        self.env.emit_event(
            "item_initialized", 
            {"item_id": item_id, "state": initial_state, "by": caller, "at": current_time}
        )
        return True

    @external
    def transition_item(
        self, 
        caller: Address, 
        item_id: Bytes, 
        to_state: Symbol, 
        guard_args: Vec
    ) -> Bool:
        """
        Transitions an item to a new state if rules allow and guards validate.
        
        Args:
            caller: Account driving the transition.
            item_id: Unique byte identity of the item.
            to_state: Destination state.
            guard_args: Parameters forwarded to the guard callback contract.
        """
        caller.require_auth()
        self._require_initialized()
        
        item_key = "item_st:" + str(item_id)
        if not self.storage.has(item_key):
            raise ContractError.ITEM_NOT_INITIALIZED
            
        from_state = self.storage.get(item_key)
        
        # Verify transition definition
        trans_key = "trans:" + str(from_state) + ":" + str(to_state)
        if not self.storage.get(trans_key, False):
            raise ContractError.TRANSITION_NOT_ALLOWED
            
        # Verify Guard callback if present
        guard = self.storage.get("guard:" + str(from_state) + ":" + str(to_state))
        admin = self.storage.get("admin")
        
        if guard != admin:
            # Trigger external validation
            # Expects method: validate_transition(item_id: Bytes, from_st: Symbol, to_st: Symbol, caller: Address, args: Vec) -> Bool
            cb_args = Vec()
            cb_args.append(item_id)
            cb_args.append(from_state)
            cb_args.append(to_state)
            cb_args.append(caller)
            cb_args.append(guard_args)
            
            valid = self.env.invoke_contract(guard, Symbol("validate_transition"), cb_args)
            # In Soroban, call might throw, or return Bool. Check result.
            if not valid:
                raise ContractError.GUARD_VALIDATION_FAILED
                
        # Perform state update
        current_time = self.env.ledger().timestamp()
        self.storage.set(item_key, to_state)
        self.storage.set("item_time:" + str(item_id) + ":" + str(to_state), current_time)
        
        self.env.emit_event(
            "item_transitioned", 
            {
                "item_id": item_id, 
                "from_state": from_state, 
                "to_state": to_state, 
                "at": current_time
            }
        )
        return True

    @view
    def get_item_state(self, item_id: Bytes) -> Symbol:
        """
        Queries the current active state of a workflow item.
        """
        self._require_initialized()
        item_key = "item_st:" + str(item_id)
        if not self.storage.has(item_key):
            raise ContractError.ITEM_NOT_INITIALIZED
        return self.storage.get(item_key)

    @view
    def get_state_timestamp(self, item_id: Bytes, state: Symbol) -> U64:
        """
        Queries when an item transitioned to a specific state. Returns 0 if never.
        """
        self._require_initialized()
        time_key = "item_time:" + str(item_id) + ":" + str(state)
        return self.storage.get(time_key, U64(0))

    @view
    def is_transition_allowed(self, from_state: Symbol, to_state: Symbol) -> Bool:
        """
        Checks if a transition rule is registered in the matrix.
        """
        self._require_initialized()
        trans_key = "trans:" + str(from_state) + ":" + str(to_state)
        return self.storage.get(trans_key, False)

    # Internal helpers
    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED
