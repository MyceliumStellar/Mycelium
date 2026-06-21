"""
BatchExecutor — Batch transaction lists, rollback policies, gas limits verification, priority rules.

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
    BATCH_SIZE_EXCEEDED = 4
    CALL_FAILED = 5
    GAS_LIMIT_EXCEEDED = 6
    PRIORITY_VIOLATION = 7
    PAUSED = 8
    INVALID_POLICY = 9

@contract
class BatchExecutor:
    """
    Batches multiple contract calls into a single transaction execution context.
    
    Enforces priority sorting, total batch gas limits, and rollback policies.
    """
    
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, max_batch_size: U64, total_gas_limit: U64):
        """
        Initializes the batch executor.
        
        Args:
            admin: Admin address controlling operational parameters.
            max_batch_size: Maximum allowed transactions in a single batch.
            total_gas_limit: Maximum combined gas allocation allowed for a batch.
        """
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED
            
        self.storage.set("admin", admin)
        self.storage.set("max_batch_size", max_batch_size)
        self.storage.set("total_gas_limit", total_gas_limit)
        self.storage.set("initialized", True)
        self.storage.set("paused", False)
        
        self.env.emit_event(
            "initialized", 
            {"admin": admin, "max_batch_size": max_batch_size, "total_gas": total_gas_limit}
        )

    @external
    def execute_batch(
        self, 
        caller: Address, 
        calls: Vec, 
        rollback_policy: Symbol
    ) -> Vec:
        """
        Executes a series of contract calls.
        
        Enforces batch parameters, gas constraints, and priority sequencing.
        
        Args:
            caller: Instantiating account address.
            calls: Vec of Maps representing the transactions.
                   Each map has keys: "target" (Address), "method" (Symbol),
                   "args" (Vec), "gas" (U64), "priority" (U64).
            rollback_policy: Policies: Symbol("REVERT_ALL"), Symbol("STOP_ON_ERROR").
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()
        
        num_calls = len(calls)
        max_size = self.storage.get("max_batch_size", U64(0))
        if num_calls > max_size or num_calls == 0:
            raise ContractError.BATCH_SIZE_EXCEEDED
            
        # Verify rollback policy validity
        if rollback_policy != Symbol("REVERT_ALL") and rollback_policy != Symbol("STOP_ON_ERROR"):
            raise ContractError.INVALID_POLICY
            
        # Verify priority and gas constraints
        total_gas_limit = self.storage.get("total_gas_limit", U64(0))
        accumulated_gas = U64(0)
        last_priority = U64(999999) # Enforce descending priority order (highest first)
        
        i = 0
        while i < num_calls:
            call_map = calls[i]
            priority = call_map.get(Symbol("priority"))
            gas = call_map.get(Symbol("gas"))
            
            # Priority Check: must be descending (or equal) to enforce ordering rules
            if priority > last_priority:
                raise ContractError.PRIORITY_VIOLATION
            last_priority = priority
            
            # Gas check
            accumulated_gas += gas
            if accumulated_gas > total_gas_limit:
                raise ContractError.GAS_LIMIT_EXCEEDED
            i += 1
            
        # Execution loop
        results = Vec()
        execution_successful = True
        
        i = 0
        while i < num_calls:
            call_map = calls[i]
            target = call_map.get(Symbol("target"))
            method = call_map.get(Symbol("method"))
            args = call_map.get(Symbol("args"))
            
            # Simulating call invocation
            # In Soroban, dynamic calls throw errors that immediately revert.
            # To handle STOP_ON_ERROR, we check if rollback policy permits reverting the whole batch.
            # Real smart contracts run inside a transactional VM.
            try:
                ret = self.env.invoke_contract(target, method, args)
                results.append(ret)
                
                self.env.emit_event(
                    "batch_call_success", 
                    {"target": target, "method": method, "index": U64(i)}
                )
            except Exception as e:
                execution_successful = False
                self.env.emit_event(
                    "batch_call_failed", 
                    {"target": target, "method": method, "index": U64(i)}
                )
                
                if rollback_policy == Symbol("REVERT_ALL"):
                    raise ContractError.CALL_FAILED
                elif rollback_policy == Symbol("STOP_ON_ERROR"):
                    # Halt further execution but do not revert prior successes
                    break
                    
            i += 1
            
        self.env.emit_event(
            "batch_executed", 
            {"caller": caller, "calls_count": U64(i), "success": execution_successful}
        )
        return results

    @external
    def set_parameters(
        self, 
        caller: Address, 
        max_batch_size: U64, 
        total_gas_limit: U64
    ) -> Bool:
        """
        Updates batch parameters.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        self.storage.set("max_batch_size", max_batch_size)
        self.storage.set("total_gas_limit", total_gas_limit)
        
        self.env.emit_event(
            "parameters_updated", 
            {"max_batch_size": max_batch_size, "total_gas_limit": total_gas_limit}
        )
        return True

    @external
    def set_paused(self, caller: Address, paused: Bool) -> Bool:
        """
        Pauses or unpauses batch execution.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        self.storage.set("paused", paused)
        self.env.emit_event("paused_state_changed", {"paused": paused})
        return True

    @view
    def get_parameters(self) -> Map:
        """
        Returns the operational thresholds.
        """
        self._require_initialized()
        params = Map()
        params.set(Symbol("max_batch_size"), self.storage.get("max_batch_size"))
        params.set(Symbol("total_gas_limit"), self.storage.get("total_gas_limit"))
        params.set(Symbol("paused"), self.storage.get("paused"))
        return params

    # Internal helpers
    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_not_paused(self):
        if self.storage.get("paused", False):
            raise ContractError.PAUSED
