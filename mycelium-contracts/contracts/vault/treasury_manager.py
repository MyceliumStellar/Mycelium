"""
Treasury Manager — Multi-sig controlled treasury with spending limits and balance buffers.

Mycelium Smart Contract for Stellar
Enforces strict treasury policies:
1. Managers can spend tokens within weekly and single-transaction limits.
2. Operations exceeding limits or breaching the minimum balance buffer require multi-sig signer overrides.
3. Automated recurring expense profiles (e.g. salaries) can be processed after their lock periods.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    INVALID_PARAMETERS = 4
    EXCEEDS_SINGLE_LIMIT = 5
    EXCEEDS_WEEKLY_LIMIT = 6
    BUFFER_BREACHED = 7
    TX_NOT_FOUND = 8
    TX_ALREADY_EXECUTED = 9
    ALREADY_APPROVED = 10
    INSUFFICIENT_APPROVALS = 11
    EXPENSE_NOT_FOUND = 12
    EXPENSE_TIMELOCK_ACTIVE = 13
    INSUFFICIENT_TREASURY_BALANCE = 14


@contract
class TreasuryManager:
    """
    Enterprise treasury manager with multi-signature overrides, policy checks,
    spending rate-limits, and scheduled payouts.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        signers: Vec,
        multisig_threshold: U64,
    ):
        """Initialize the treasury manager with signers and threshold."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if len(signers) == 0 or multisig_threshold == 0 or multisig_threshold > len(signers):
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("admin", admin)
        self.storage.set("signer_threshold", multisig_threshold)
        self.storage.set("signer_count", U64(len(signers)))

        for i in range(len(signers)):
            s = signers[i]
            self.storage.set(f"signer:exists:{s}", True)
            self.storage.set(f"signer:index:{i}", s)

        self.storage.set("tx_count", U64(0))
        self.storage.set("expense_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "threshold": multisig_threshold,
        })

    @external
    def add_manager(self, admin: Address, manager: Address):
        """Register a manager allowed to initiate limit-restricted transactions."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        self.storage.set(f"manager:exists:{manager}", True)
        self.env.emit_event("manager_added", {"manager": manager})

    @external
    def set_token_policy(
        self,
        admin: Address,
        token: Address,
        single_limit: U128,
        weekly_limit: U128,
        min_buffer: U128,
    ):
        """Set spending policies (single Tx limit, weekly cap, minimum buffer) for a token."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        self.storage.set(f"limit:single:{token}", single_limit)
        self.storage.set(f"limit:weekly:{token}", weekly_limit)
        self.storage.set(f"buffer:{token}", min_buffer)

        self.env.emit_event("policy_updated", {
            "token": token,
            "single_limit": single_limit,
            "weekly_limit": weekly_limit,
            "min_buffer": min_buffer,
        })

    @external
    def deposit(self, caller: Address, token: Address, amount: U128):
        """Deposit funds into the treasury contract."""
        caller.require_auth()
        self._require_initialized()

        if amount == 0:
            raise ContractError.INVALID_PARAMETERS

        self.env.transfer(token, caller, self.env.current_contract(), amount)

        bal = self.storage.get(f"treasury_balance:{token}", U128(0))
        self.storage.set(f"treasury_balance:{token}", bal + amount)

        self.env.emit_event("deposited", {
            "token": token,
            "amount": amount,
            "sender": caller,
        })

    @external
    def manager_spend(
        self,
        manager: Address,
        recipient: Address,
        token: Address,
        amount: U128,
    ):
        """Manager spends tokens within policy limits. Fails if limits or buffers are breached."""
        manager.require_auth()
        self._require_initialized()

        if not self.storage.get(f"manager:exists:{manager}", False):
            raise ContractError.UNAUTHORIZED

        # 1. Single limit check
        single_lim = self.storage.get(f"limit:single:{token}", U128(0))
        if amount > single_lim:
            raise ContractError.EXCEEDS_SINGLE_LIMIT

        # 2. Weekly limit check
        current_time = self.env.ledger().timestamp()
        last_reset = self.storage.get(f"weekly_reset:{token}", U64(0))
        weekly_spent = self.storage.get(f"weekly_spent:{token}", U128(0))

        if current_time - last_reset > 604800:
            weekly_spent = U128(0)
            self.storage.set(f"weekly_reset:{token}", current_time)

        weekly_lim = self.storage.get(f"limit:weekly:{token}", U128(0))
        if weekly_spent + amount > weekly_lim:
            raise ContractError.EXCEEDS_WEEKLY_LIMIT

        # 3. Buffer check
        bal = self.storage.get(f"treasury_balance:{token}", U128(0))
        if amount > bal:
            raise ContractError.INSUFFICIENT_TREASURY_BALANCE

        buffer = self.storage.get(f"buffer:{token}", U128(0))
        if bal - amount < buffer:
            raise ContractError.BUFFER_BREACHED

        # Update spend parameters and perform transfer
        self.storage.set(f"weekly_spent:{token}", weekly_spent + amount)
        self.storage.set(f"treasury_balance:{token}", bal - amount)

        self.env.transfer(token, self.env.current_contract(), recipient, amount)

        self.env.emit_event("treasury_spend", {
            "recipient": recipient,
            "token": token,
            "amount": amount,
            "by": manager,
        })

    # ── Multi-Sig Override Mechanisms ───────────────────────────────

    @external
    def propose_transaction(
        self,
        proposer: Address,
        recipient: Address,
        token: Address,
        amount: U128,
        description: Symbol,
    ) -> U64:
        """Propose a transaction that breaches standard policies, requiring multi-sig approvals."""
        proposer.require_auth()
        self._require_initialized()

        # Proposer must be manager, admin, or signer
        is_signer = self.storage.get(f"signer:exists:{proposer}", False)
        is_manager = self.storage.get(f"manager:exists:{proposer}", False)
        is_admin = (proposer == self.storage.get("admin"))
        
        if not (is_signer or is_manager or is_admin):
            raise ContractError.UNAUTHORIZED

        tx_id = self.storage.get("tx_count", U64(0)) + U64(1)
        self.storage.set("tx_count", tx_id)

        self.storage.set(f"tx:recipient:{tx_id}", recipient)
        self.storage.set(f"tx:token:{tx_id}", token)
        self.storage.set(f"tx:amount:{tx_id}", amount)
        self.storage.set(f"tx:approvals:{tx_id}", U64(0))
        self.storage.set(f"tx:executed:{tx_id}", False)
        self.storage.set(f"tx:desc:{tx_id}", description)

        self.env.emit_event("tx_proposed", {
            "tx_id": tx_id,
            "recipient": recipient,
            "token": token,
            "amount": amount,
        })

        return tx_id

    @external
    def approve_transaction(self, signer: Address, tx_id: U64):
        """Approve a proposed override transaction."""
        signer.require_auth()
        self._require_initialized()

        if not self.storage.get(f"signer:exists:{signer}", False):
            raise ContractError.UNAUTHORIZED

        self._check_tx_exists(tx_id)

        if self.storage.get(f"tx:executed:{tx_id}", False):
            raise ContractError.TX_ALREADY_EXECUTED

        if self.storage.get(f"tx:approver:{tx_id}:{signer}", False):
            raise ContractError.ALREADY_APPROVED

        self.storage.set(f"tx:approver:{tx_id}:{signer}", True)
        
        approvals = self.storage.get(f"tx:approvals:{tx_id}", U64(0)) + U64(1)
        self.storage.set(f"tx:approvals:{tx_id}", approvals)

        self.env.emit_event("tx_approved", {
            "tx_id": tx_id,
            "signer": signer,
            "current_approvals": approvals,
        })

    @external
    def execute_transaction(self, caller: Address, tx_id: U64):
        """Execute a transaction that has collected sufficient multi-sig approvals (bypasses limits & buffers)."""
        self._require_initialized()
        self._check_tx_exists(tx_id)

        if self.storage.get(f"tx:executed:{tx_id}", False):
            raise ContractError.TX_ALREADY_EXECUTED

        approvals = self.storage.get(f"tx:approvals:{tx_id}", U64(0))
        threshold = self.storage.get("signer_threshold")
        if approvals < threshold:
            raise ContractError.INSUFFICIENT_APPROVALS

        recipient = self.storage.get(f"tx:recipient:{tx_id}")
        token = self.storage.get(f"tx:token:{tx_id}")
        amount = self.storage.get(f"tx:amount:{tx_id}")

        bal = self.storage.get(f"treasury_balance:{token}", U128(0))
        if amount > bal:
            raise ContractError.INSUFFICIENT_TREASURY_BALANCE

        # Update balance and execute
        self.storage.set(f"tx:executed:{tx_id}", True)
        self.storage.set(f"treasury_balance:{token}", bal - amount)

        self.env.transfer(token, self.env.current_contract(), recipient, amount)

        self.env.emit_event("tx_executed", {
            "tx_id": tx_id,
            "recipient": recipient,
            "token": token,
            "amount": amount,
        })

    # ── Recurring Expenses ────────────────────────────────────────────

    @external
    def register_recurring_expense(
        self,
        admin: Address,
        recipient: Address,
        token: Address,
        amount: U128,
        interval: U64,
    ) -> U64:
        """Register a recurring payout line (e.g. salary payment)."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        if amount == 0 or interval == 0:
            raise ContractError.INVALID_PARAMETERS

        expense_id = self.storage.get("expense_count", U64(0)) + U64(1)
        self.storage.set("expense_count", expense_id)

        self.storage.set(f"expense:recipient:{expense_id}", recipient)
        self.storage.set(f"expense:token:{expense_id}", token)
        self.storage.set(f"expense:amount:{expense_id}", amount)
        self.storage.set(f"expense:interval:{expense_id}", interval)
        self.storage.set(f"expense:last_pay:{expense_id}", U64(0))  # Can pay immediately the first time
        self.storage.set(f"expense:active:{expense_id}", True)

        self.env.emit_event("recurring_registered", {
            "expense_id": expense_id,
            "recipient": recipient,
            "token": token,
            "amount": amount,
            "interval": interval,
        })

        return expense_id

    @external
    def pay_recurring_expense(self, caller: Address, expense_id: U64):
        """Process a recurring expense payment if its interval has elapsed (enforces buffer check)."""
        self._require_initialized()
        self._check_expense_exists(expense_id)

        if not self.storage.get(f"expense:active:{expense_id}", False):
            raise ContractError.EXPENSE_NOT_FOUND

        interval = self.storage.get(f"expense:interval:{expense_id}")
        last_pay = self.storage.get(f"expense:last_pay:{expense_id}")
        current_time = self.env.ledger().timestamp()

        if current_time < last_pay + interval:
            raise ContractError.EXPENSE_TIMELOCK_ACTIVE

        token = self.storage.get(f"expense:token:{expense_id}")
        amount = self.storage.get(f"expense:amount:{expense_id}")
        recipient = self.storage.get(f"expense:recipient:{expense_id}")

        bal = self.storage.get(f"treasury_balance:{token}", U128(0))
        if amount > bal:
            raise ContractError.INSUFFICIENT_TREASURY_BALANCE

        # Recurring expenses must also respect buffers
        buffer = self.storage.get(f"buffer:{token}", U128(0))
        if bal - amount < buffer:
            raise ContractError.BUFFER_BREACHED

        # Update metrics and process payment
        self.storage.set(f"expense:last_pay:{expense_id}", current_time)
        self.storage.set(f"treasury_balance:{token}", bal - amount)

        self.env.transfer(token, self.env.current_contract(), recipient, amount)

        self.env.emit_event("recurring_paid", {
            "expense_id": expense_id,
            "recipient": recipient,
            "amount": amount,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_treasury_balance(self, token: Address) -> U128:
        """Get internal treasury balance registry for a token."""
        return self.storage.get(f"treasury_balance:{token}", U128(0))

    @view
    def get_policy(self, token: Address) -> Map:
        """Get the spend policy details for a token."""
        return {
            "single_limit": self.storage.get(f"limit:single:{token}", U128(0)),
            "weekly_limit": self.storage.get(f"limit:weekly:{token}", U128(0)),
            "min_buffer": self.storage.get(f"buffer:{token}", U128(0)),
            "weekly_spent": self.storage.get(f"weekly_spent:{token}", U128(0)),
        }

    @view
    def get_proposal(self, tx_id: U64) -> Map:
        """Get proposal status and approvals."""
        self._check_tx_exists(tx_id)
        return {
            "recipient": self.storage.get(f"tx:recipient:{tx_id}"),
            "token": self.storage.get(f"tx:token:{tx_id}"),
            "amount": self.storage.get(f"tx:amount:{tx_id}"),
            "approvals": self.storage.get(f"tx:approvals:{tx_id}"),
            "executed": self.storage.get(f"tx:executed:{tx_id}"),
        }

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _check_tx_exists(self, tx_id: U64):
        total = self.storage.get("tx_count", U64(0))
        if tx_id == 0 or tx_id > total:
            raise ContractError.TX_NOT_FOUND

    def _check_expense_exists(self, expense_id: U64):
        total = self.storage.get("expense_count", U64(0))
        if expense_id == 0 or expense_id > total:
            raise ContractError.EXPENSE_NOT_FOUND
