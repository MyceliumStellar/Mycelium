"""
Timelock Controller — Time-delayed execution with role-based access control.

Mycelium Smart Contract for Stellar

Implements a timelock with proposer/executor/canceller roles, configurable
minimum delay, operation scheduling with salt, batch operations, predecessor
dependencies, and operation state tracking (Unset → Waiting → Ready → Done).
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    INVALID_DELAY = 4
    OPERATION_ALREADY_SCHEDULED = 5
    OPERATION_NOT_FOUND = 6
    OPERATION_NOT_READY = 7
    OPERATION_ALREADY_DONE = 8
    PREDECESSOR_NOT_DONE = 9
    DELAY_TOO_SHORT = 10
    DELAY_TOO_LONG = 11
    BATCH_LENGTH_MISMATCH = 12
    EMPTY_BATCH = 13
    INVALID_ROLE = 14
    ROLE_ALREADY_GRANTED = 15
    ROLE_NOT_HELD = 16
    CANNOT_REVOKE_LAST_ADMIN = 17
    SELF_ADMIN_REVOKE = 18
    OPERATION_CANCELLED = 19
    DELAY_OVERFLOW = 20
    EXECUTION_FAILED = 21


class OperationState:
    UNSET = 0
    WAITING = 1
    READY = 2
    DONE = 3


class Role:
    ADMIN = "admin"
    PROPOSER = "proposer"
    EXECUTOR = "executor"
    CANCELLER = "canceller"


MIN_DELAY = 86400          # 24 hours in seconds
MAX_DELAY = 2592000        # 30 days in seconds
MAX_BATCH_SIZE = 25


@contract
class TimelockController:
    """Time-delayed execution controller with role separation for
    proposers, executors, cancellers, and an admin role for management."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ------------------------------------------------------------------ #
    #  Initialization                                                      #
    # ------------------------------------------------------------------ #

    @external
    def initialize(
        self,
        admin: Address,
        min_delay: U64,
        proposers: Vec,
        executors: Vec,
        cancellers: Vec,
        open_executor: Bool,
    ):
        """Initialize the timelock with roles and minimum delay.

        Args:
            admin: Admin who can manage roles and update delay.
            min_delay: Minimum delay in seconds (must be between 24h and 30d).
            proposers: Initial addresses with the proposer role.
            executors: Initial addresses with the executor role.
            cancellers: Initial addresses with the canceller role.
            open_executor: If True, any address may execute ready operations.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()
        self._validate_delay(min_delay)

        self.storage.set("admin", admin)
        self.storage.set("min_delay", min_delay)
        self.storage.set("open_executor", open_executor)
        self.storage.set("operation_count", U64(0))

        self._grant_role_internal(admin, Role.ADMIN)
        for proposer in proposers:
            self._grant_role_internal(proposer, Role.PROPOSER)
        for executor in executors:
            self._grant_role_internal(executor, Role.EXECUTOR)
        for canceller in cancellers:
            self._grant_role_internal(canceller, Role.CANCELLER)

        self.storage.set("admin_count", U64(1))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "min_delay": min_delay,
            "open_executor": open_executor,
        })

    # ------------------------------------------------------------------ #
    #  Schedule operations                                                 #
    # ------------------------------------------------------------------ #

    @external
    def schedule(
        self,
        proposer: Address,
        target: Address,
        value: U128,
        calldata: Bytes,
        predecessor: Bytes,
        salt: Bytes,
        delay: U64,
    ) -> Bytes:
        """Schedule a single operation for future execution.

        Args:
            proposer: Must hold the proposer role.
            target: Contract to call.
            value: Native token value to send.
            calldata: Encoded function call.
            predecessor: Operation ID that must be done first (empty for none).
            salt: Unique salt to differentiate identical operations.
            delay: Delay in seconds (must be ≥ min_delay).

        Returns:
            The unique operation ID (hash).
        """
        self._require_initialized()
        proposer.require_auth()
        self._require_role(proposer, Role.PROPOSER)

        min_delay = self.storage.get("min_delay")
        if delay < min_delay:
            raise ContractError.DELAY_TOO_SHORT

        now = self.env.ledger().timestamp()
        if now + delay < now:
            raise ContractError.DELAY_OVERFLOW

        op_id = self._hash_operation(target, value, calldata, predecessor, salt)
        existing_state = self._get_operation_state(op_id)
        if existing_state != OperationState.UNSET:
            raise ContractError.OPERATION_ALREADY_SCHEDULED

        ready_at = now + delay
        operation = {
            "target": target,
            "value": value,
            "calldata": calldata,
            "predecessor": predecessor,
            "salt": salt,
            "ready_at": ready_at,
            "done": False,
            "cancelled": False,
            "scheduled_by": proposer,
            "scheduled_at": now,
            "is_batch": False,
        }

        self.storage.set(("operation", op_id), operation)
        op_count = self.storage.get("operation_count") + U64(1)
        self.storage.set("operation_count", op_count)

        self.env.emit_event("operation_scheduled", {
            "operation_id": op_id,
            "target": target,
            "value": value,
            "ready_at": ready_at,
            "predecessor": predecessor,
        })

        return op_id

    @external
    def schedule_batch(
        self,
        proposer: Address,
        targets: Vec,
        values: Vec,
        calldatas: Vec,
        predecessor: Bytes,
        salt: Bytes,
        delay: U64,
    ) -> Bytes:
        """Schedule a batch of operations executed atomically.

        Args:
            proposer: Must hold the proposer role.
            targets: Contracts to call.
            values: Native token values.
            calldatas: Encoded function calls.
            predecessor: Operation ID that must be done first.
            salt: Unique salt.
            delay: Delay in seconds.

        Returns:
            The batch operation ID.
        """
        self._require_initialized()
        proposer.require_auth()
        self._require_role(proposer, Role.PROPOSER)

        if len(targets) == 0:
            raise ContractError.EMPTY_BATCH
        if len(targets) != len(values) or len(targets) != len(calldatas):
            raise ContractError.BATCH_LENGTH_MISMATCH
        if len(targets) > MAX_BATCH_SIZE:
            raise ContractError.EMPTY_BATCH

        min_delay = self.storage.get("min_delay")
        if delay < min_delay:
            raise ContractError.DELAY_TOO_SHORT

        now = self.env.ledger().timestamp()
        if now + delay < now:
            raise ContractError.DELAY_OVERFLOW

        op_id = self._hash_batch_operation(targets, values, calldatas, predecessor, salt)
        existing_state = self._get_operation_state(op_id)
        if existing_state != OperationState.UNSET:
            raise ContractError.OPERATION_ALREADY_SCHEDULED

        ready_at = now + delay
        operation = {
            "targets": targets,
            "values": values,
            "calldatas": calldatas,
            "predecessor": predecessor,
            "salt": salt,
            "ready_at": ready_at,
            "done": False,
            "cancelled": False,
            "scheduled_by": proposer,
            "scheduled_at": now,
            "is_batch": True,
            "batch_size": U64(len(targets)),
        }

        self.storage.set(("operation", op_id), operation)
        op_count = self.storage.get("operation_count") + U64(1)
        self.storage.set("operation_count", op_count)

        self.env.emit_event("batch_scheduled", {
            "operation_id": op_id,
            "batch_size": U64(len(targets)),
            "ready_at": ready_at,
            "predecessor": predecessor,
        })

        return op_id

    # ------------------------------------------------------------------ #
    #  Execute operations                                                  #
    # ------------------------------------------------------------------ #

    @external
    def execute(
        self,
        executor: Address,
        target: Address,
        value: U128,
        calldata: Bytes,
        predecessor: Bytes,
        salt: Bytes,
    ):
        """Execute a scheduled operation that is now ready.

        Args:
            executor: Must hold executor role (or open_executor is True).
            target: Contract to call.
            value: Native token value.
            calldata: Encoded function call.
            predecessor: Predecessor operation ID.
            salt: Salt used during scheduling.
        """
        self._require_initialized()
        executor.require_auth()
        self._check_executor_permission(executor)

        op_id = self._hash_operation(target, value, calldata, predecessor, salt)
        self._before_execute(op_id)
        self._check_predecessor(predecessor)

        success = self.env.invoke_contract(target, calldata, value)
        if not success:
            raise ContractError.EXECUTION_FAILED

        self._after_execute(op_id)

        self.env.emit_event("operation_executed", {
            "operation_id": op_id,
            "executor": executor,
        })

    @external
    def execute_batch(
        self,
        executor: Address,
        targets: Vec,
        values: Vec,
        calldatas: Vec,
        predecessor: Bytes,
        salt: Bytes,
    ):
        """Execute a scheduled batch operation atomically.

        Args:
            executor: Must hold executor role (or open_executor is True).
            targets: Contracts to call.
            values: Native token values.
            calldatas: Encoded function calls.
            predecessor: Predecessor operation ID.
            salt: Salt used during scheduling.
        """
        self._require_initialized()
        executor.require_auth()
        self._check_executor_permission(executor)

        if len(targets) == 0:
            raise ContractError.EMPTY_BATCH
        if len(targets) != len(values) or len(targets) != len(calldatas):
            raise ContractError.BATCH_LENGTH_MISMATCH

        op_id = self._hash_batch_operation(targets, values, calldatas, predecessor, salt)
        self._before_execute(op_id)
        self._check_predecessor(predecessor)

        for i in range(len(targets)):
            success = self.env.invoke_contract(targets[i], calldatas[i], values[i])
            if not success:
                raise ContractError.EXECUTION_FAILED

        self._after_execute(op_id)

        self.env.emit_event("batch_executed", {
            "operation_id": op_id,
            "executor": executor,
            "batch_size": U64(len(targets)),
        })

    # ------------------------------------------------------------------ #
    #  Cancel                                                              #
    # ------------------------------------------------------------------ #

    @external
    def cancel(self, canceller: Address, operation_id: Bytes):
        """Cancel a scheduled (not yet executed) operation.

        Args:
            canceller: Must hold canceller role.
            operation_id: The operation to cancel.
        """
        self._require_initialized()
        canceller.require_auth()
        self._require_role(canceller, Role.CANCELLER)

        state = self._get_operation_state(operation_id)
        if state == OperationState.UNSET:
            raise ContractError.OPERATION_NOT_FOUND
        if state == OperationState.DONE:
            raise ContractError.OPERATION_ALREADY_DONE

        operation = self.storage.get(("operation", operation_id))
        if operation["cancelled"]:
            raise ContractError.OPERATION_CANCELLED

        operation["cancelled"] = True
        self.storage.set(("operation", operation_id), operation)

        self.env.emit_event("operation_cancelled", {
            "operation_id": operation_id,
            "cancelled_by": canceller,
        })

    # ------------------------------------------------------------------ #
    #  Role management                                                     #
    # ------------------------------------------------------------------ #

    @external
    def grant_role(self, admin: Address, account: Address, role: Symbol):
        """Grant a role to an account. Only admin."""
        self._require_initialized()
        self._require_admin(admin)

        if role not in [Role.PROPOSER, Role.EXECUTOR, Role.CANCELLER, Role.ADMIN]:
            raise ContractError.INVALID_ROLE

        has_role = self.storage.get(("role", role, account), False)
        if has_role:
            raise ContractError.ROLE_ALREADY_GRANTED

        self._grant_role_internal(account, role)

        if role == Role.ADMIN:
            admin_count = self.storage.get("admin_count", U64(0))
            self.storage.set("admin_count", admin_count + U64(1))

        self.env.emit_event("role_granted", {
            "account": account,
            "role": role,
            "granted_by": admin,
        })

    @external
    def revoke_role(self, admin: Address, account: Address, role: Symbol):
        """Revoke a role from an account. Only admin."""
        self._require_initialized()
        self._require_admin(admin)

        if role not in [Role.PROPOSER, Role.EXECUTOR, Role.CANCELLER, Role.ADMIN]:
            raise ContractError.INVALID_ROLE

        has_role = self.storage.get(("role", role, account), False)
        if not has_role:
            raise ContractError.ROLE_NOT_HELD

        if role == Role.ADMIN:
            if admin == account:
                raise ContractError.SELF_ADMIN_REVOKE
            admin_count = self.storage.get("admin_count", U64(0))
            if admin_count <= U64(1):
                raise ContractError.CANNOT_REVOKE_LAST_ADMIN
            self.storage.set("admin_count", admin_count - U64(1))

        self.storage.set(("role", role, account), False)

        self.env.emit_event("role_revoked", {
            "account": account,
            "role": role,
            "revoked_by": admin,
        })

    # ------------------------------------------------------------------ #
    #  Admin config                                                        #
    # ------------------------------------------------------------------ #

    @external
    def update_min_delay(self, admin: Address, new_delay: U64):
        """Update the minimum execution delay. Only admin."""
        self._require_initialized()
        self._require_admin(admin)
        self._validate_delay(new_delay)

        old_delay = self.storage.get("min_delay")
        self.storage.set("min_delay", new_delay)

        self.env.emit_event("min_delay_updated", {
            "old_delay": old_delay,
            "new_delay": new_delay,
        })

    @external
    def set_open_executor(self, admin: Address, open_executor: Bool):
        """Toggle whether any address can execute ready operations."""
        self._require_initialized()
        self._require_admin(admin)

        self.storage.set("open_executor", open_executor)
        self.env.emit_event("open_executor_updated", {"open_executor": open_executor})

    # ------------------------------------------------------------------ #
    #  View functions                                                      #
    # ------------------------------------------------------------------ #

    @view
    def get_operation(self, operation_id: Bytes) -> Map:
        """Return full operation data with computed state."""
        operation = self.storage.get(("operation", operation_id), None)
        if operation is None:
            return {"state": OperationState.UNSET}
        operation["state"] = self._get_operation_state(operation_id)
        return operation

    @view
    def get_operation_state(self, operation_id: Bytes) -> U64:
        """Return computed state of an operation."""
        return self._get_operation_state(operation_id)

    @view
    def is_operation_ready(self, operation_id: Bytes) -> Bool:
        """Check if an operation is ready for execution."""
        return self._get_operation_state(operation_id) == OperationState.READY

    @view
    def is_operation_done(self, operation_id: Bytes) -> Bool:
        """Check if an operation has been executed."""
        return self._get_operation_state(operation_id) == OperationState.DONE

    @view
    def get_min_delay(self) -> U64:
        """Return the current minimum delay."""
        return self.storage.get("min_delay")

    @view
    def has_role(self, account: Address, role: Symbol) -> Bool:
        """Check if an account holds a given role."""
        return self.storage.get(("role", role, account), False)

    @view
    def get_operation_count(self) -> U64:
        """Return total operations scheduled (including cancelled)."""
        return self.storage.get("operation_count", U64(0))

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        caller.require_auth()
        if not self.storage.get(("role", Role.ADMIN, caller), False):
            raise ContractError.UNAUTHORIZED

    def _require_role(self, caller: Address, role: Symbol):
        if not self.storage.get(("role", role, caller), False):
            raise ContractError.UNAUTHORIZED

    def _grant_role_internal(self, account: Address, role: Symbol):
        self.storage.set(("role", role, account), True)

    def _validate_delay(self, delay: U64):
        if delay < MIN_DELAY:
            raise ContractError.DELAY_TOO_SHORT
        if delay > MAX_DELAY:
            raise ContractError.DELAY_TOO_LONG

    def _check_executor_permission(self, executor: Address):
        open_exec = self.storage.get("open_executor", False)
        if not open_exec:
            self._require_role(executor, Role.EXECUTOR)

    def _get_operation_state(self, op_id: Bytes) -> U64:
        operation = self.storage.get(("operation", op_id), None)
        if operation is None:
            return OperationState.UNSET
        if operation["cancelled"]:
            return OperationState.UNSET
        if operation["done"]:
            return OperationState.DONE
        now = self.env.ledger().timestamp()
        if now >= operation["ready_at"]:
            return OperationState.READY
        return OperationState.WAITING

    def _check_predecessor(self, predecessor: Bytes):
        if predecessor is None or len(predecessor) == 0:
            return
        pred_state = self._get_operation_state(predecessor)
        if pred_state != OperationState.DONE:
            raise ContractError.PREDECESSOR_NOT_DONE

    def _before_execute(self, op_id: Bytes):
        state = self._get_operation_state(op_id)
        if state == OperationState.UNSET:
            raise ContractError.OPERATION_NOT_FOUND
        if state == OperationState.DONE:
            raise ContractError.OPERATION_ALREADY_DONE
        if state == OperationState.WAITING:
            raise ContractError.OPERATION_NOT_READY

    def _after_execute(self, op_id: Bytes):
        operation = self.storage.get(("operation", op_id))
        operation["done"] = True
        self.storage.set(("operation", op_id), operation)

    def _hash_operation(
        self, target: Address, value: U128, calldata: Bytes,
        predecessor: Bytes, salt: Bytes
    ) -> Bytes:
        return self.env.crypto().keccak256(
            target, value, calldata, predecessor, salt
        )

    def _hash_batch_operation(
        self, targets: Vec, values: Vec, calldatas: Vec,
        predecessor: Bytes, salt: Bytes
    ) -> Bytes:
        return self.env.crypto().keccak256(
            targets, values, calldatas, predecessor, salt
        )
