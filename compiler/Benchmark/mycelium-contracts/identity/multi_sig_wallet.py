"""
Multi-Signature Wallet — Nonce-based multi-sig with daily limits and self-governed owners.

Mycelium Smart Contract for Stellar. Manages M-of-N owners, proposals queue for custom payments/calls,
enforces signature thresholds, limits single-owner daily payouts with auto-resets,
and locks admin changes to self-calls from the multi-sig.
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
    INVALID_THRESHOLD = 5
    INVALID_OWNERS = 6
    PROPOSAL_NOT_FOUND = 7
    ALREADY_EXECUTED = 8
    ALREADY_APPROVED = 9
    DAILY_LIMIT_EXCEEDED = 10
    THRESHOLD_NOT_MET = 11

@contract
class MultiSigWallet:
    """
    Nonce-based Multi-Signature wallet with daily spend limits.
    Transactions require approvals from a threshold of owners.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        owners: Vec,
        threshold: U64,
        daily_limit: U128,
        payment_token: Address
    ):
        """Initialize owners list and limits."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        num_owners = len(owners)
        if num_owners == 0 or threshold == U64(0) or threshold > num_owners:
            raise ContractError.INVALID_THRESHOLD

        self.storage.set("owners_count", num_owners)
        self.storage.set("threshold", threshold)
        self.storage.set("daily_limit", daily_limit)
        self.storage.set("payment_token", payment_token)
        self.storage.set("daily_spent", U128(0))
        self.storage.set("last_limit_reset", self._get_now())
        self.storage.set("proposal_next_id", U64(1))
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        for i in range(num_owners):
            addr = owners.get(i)
            self.storage.set(f"owner_{i}", addr)
            self.storage.set(f"is_owner_{addr}", True)

        self.env.emit_event("initialized", {
            "owners_count": num_owners,
            "threshold": threshold,
            "daily_limit": daily_limit
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause transaction proposals. Self-call (multi-sig) only."""
        caller.require_auth()
        self._require_initialized()
        self._require_self(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- OWNER MANAGEMENT (SELF-CALL ONLY) ---

    @external
    def add_owner(self, caller: Address, new_owner: Address, new_threshold: U64):
        """Add a new owner. Requires self-call (executed from the multisig proposal)."""
        caller.require_auth()
        self._require_initialized()
        self._require_self(caller)

        if self.storage.get(f"is_owner_{new_owner}", False):
            raise ContractError.INVALID_OWNERS

        count = self.storage.get("owners_count", U64(0))
        self.storage.set(f"owner_{count}", new_owner)
        self.storage.set(f"is_owner_{new_owner}", True)
        self.storage.set("owners_count", count + U64(1))

        self.update_threshold(caller, new_threshold)

        self.env.emit_event("owner_added", {"new_owner": new_owner})

    @external
    def remove_owner(self, caller: Address, owner_to_remove: Address, new_threshold: U64):
        """Remove an owner. Requires self-call."""
        caller.require_auth()
        self._require_initialized()
        self._require_self(caller)

        if not self.storage.get(f"is_owner_{owner_to_remove}", False):
            raise ContractError.INVALID_OWNERS

        self.storage.remove(f"is_owner_{owner_to_remove}")

        # Swap out owner from array
        count = self.storage.get("owners_count", U64(0))
        found = False
        for i in range(int(count)):
            addr = self.storage.get(f"owner_{i}")
            if addr == owner_to_remove:
                last_idx = count - U64(1)
                last_owner = self.storage.get(f"owner_{last_idx}")
                self.storage.set(f"owner_{i}", last_owner)
                self.storage.remove(f"owner_{last_idx}")
                self.storage.set("owners_count", last_idx)
                found = True
                break

        if not found:
            raise ContractError.INVALID_OWNERS

        self.update_threshold(caller, new_threshold)

        self.env.emit_event("owner_removed", {"removed_owner": owner_to_remove})

    @external
    def update_threshold(self, caller: Address, new_threshold: U64):
        """Modify confirmation threshold requirements. Requires self-call."""
        self._require_self(caller)
        count = self.storage.get("owners_count", U64(0))

        if new_threshold == U64(0) or new_threshold > count:
            raise ContractError.INVALID_THRESHOLD

        self.storage.set("threshold", new_threshold)
        self.env.emit_event("threshold_updated", {"new_threshold": new_threshold})

    # --- PROPOSAL QUEUE ---

    @external
    def propose_transaction(self, caller: Address, destination: Address, amount: U128) -> U64:
        """Propose a transaction from the wallet to destination."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()
        self._require_owner(caller)

        prop_id = self.storage.get("proposal_next_id", U64(1))
        
        self.storage.set(f"prop_dest_{prop_id}", destination)
        self.storage.set(f"prop_amt_{prop_id}", amount)
        self.storage.set(f"prop_executed_{prop_id}", False)
        self.storage.set(f"prop_approvals_{prop_id}", U64(1))
        self.storage.set(f"prop_approved_{prop_id}_{caller}", True)

        self.storage.set("proposal_next_id", prop_id + U64(1))

        self.env.emit_event("transaction_proposed", {
            "proposal_id": prop_id,
            "proposer": caller,
            "destination": destination,
            "amount": amount
        })

        return prop_id

    @external
    def approve_transaction(self, caller: Address, proposal_id: U64):
        """Approve an active transaction proposal."""
        caller.require_auth()
        self._require_initialized()
        self._require_owner(caller)

        # Check proposal existence
        self._require_proposal_active(proposal_id)

        if self.storage.get(f"prop_approved_{proposal_id}_{caller}", False):
            raise ContractError.ALREADY_APPROVED

        self.storage.set(f"prop_approved_{proposal_id}_{caller}", True)
        approvals = self.storage.get(f"prop_approvals_{proposal_id}", U64(0)) + U64(1)
        self.storage.set(f"prop_approvals_{proposal_id}", approvals)

        self.env.emit_event("transaction_approved", {
            "proposal_id": proposal_id,
            "approver": caller,
            "total_approvals": approvals
        })

    @external
    def execute_transaction(self, caller: Address, proposal_id: U64):
        """Execute the transaction after threshold approvals are met."""
        self._require_initialized()
        self._require_proposal_active(proposal_id)

        approvals = self.storage.get(f"prop_approvals_{proposal_id}", U64(0))
        threshold = self.storage.get("threshold", U64(0))
        if approvals < threshold:
            raise ContractError.THRESHOLD_NOT_MET

        self.storage.set(f"prop_executed_{proposal_id}", True)

        # Execute payment
        dest = self.storage.get(f"prop_dest_{proposal_id}")
        amount = self.storage.get(f"prop_amt_{proposal_id}", U128(0))
        token = self.storage.get("payment_token")

        if amount > U128(0):
            self.env.call(token, "transfer", self.env.current_contract_address(), dest, amount)

        self.env.emit_event("transaction_executed", {
            "proposal_id": proposal_id,
            "destination": dest,
            "amount": amount
        })

    # --- DAILY LIMIT (SINGLE-SIGNATURE EXECUTION) ---

    @external
    def execute_single_with_daily_limit(self, caller: Address, destination: Address, amount: U128):
        """
        Execute a payment instantly under single owner approval if within the daily limits.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()
        self._require_owner(caller)

        now = self._get_now()
        last_reset = self.storage.get("last_limit_reset", U64(0))
        daily_spent = self.storage.get("daily_spent", U128(0))
        limit = self.storage.get("daily_limit", U128(0))

        # Reset daily window if 24 hours have passed
        if now >= last_reset + U64(86400):
            daily_spent = U128(0)
            self.storage.set("last_limit_reset", now)

        if daily_spent + amount > limit:
            raise ContractError.DAILY_LIMIT_EXCEEDED

        # Record spent progress
        self.storage.set("daily_spent", daily_spent + amount)

        # Execute payment
        token = self.storage.get("payment_token")
        if amount > U128(0):
            self.env.call(token, "transfer", self.env.current_contract_address(), destination, amount)

        self.env.emit_event("daily_limit_executed", {
            "caller": caller,
            "destination": destination,
            "amount": amount,
            "new_spent": daily_spent + amount
        })

    # --- VIEWS ---

    @view
    def is_owner(self, account: Address) -> Bool:
        """Check if address is owner."""
        return self.storage.get(f"is_owner_{account}", False)

    @view
    def get_owners(self) -> Vec:
        """Query complete list of owners."""
        self._require_initialized()
        res = Vec(self.env)
        count = self.storage.get("owners_count", U64(0))
        for i in range(int(count)):
            res.push_back(self.storage.get(f"owner_{i}"))
        return res

    @view
    def get_proposal(self, proposal_id: U64) -> Map:
        """Inspect proposed transaction details."""
        res = Map(self.env)
        dest = self.storage.get(f"prop_dest_{proposal_id}")
        if dest is not None:
            res.set("destination", dest)
            res.set("amount", self.storage.get(f"prop_amt_{proposal_id}"))
            res.set("executed", self.storage.get(f"prop_executed_{proposal_id}"))
            res.set("approvals", self.storage.get(f"prop_approvals_{proposal_id}"))
        return res

    @view
    def get_daily_limit_status(self) -> Map:
        """Check daily spend and remaining limit."""
        res = Map(self.env)
        limit = self.storage.get("daily_limit", U128(0))
        
        now = self._get_now()
        last_reset = self.storage.get("last_limit_reset", U64(0))
        spent = self.storage.get("daily_spent", U128(0))
        if now >= last_reset + U64(86400):
            spent = U128(0)

        res.set("limit", limit)
        res.set("spent", spent)
        res.set("remaining", limit - spent)
        res.set("reset_time", last_reset + U64(86400))
        return res

    # --- INTERNAL HELPERS ---

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_not_paused(self):
        if self.storage.get("paused", False):
            raise ContractError.PAUSED

    def _require_owner(self, account: Address):
        if not self.storage.get(f"is_owner_{account}", False):
            raise ContractError.UNAUTHORIZED

    def _require_self(self, account: Address):
        if account != self.env.current_contract_address():
            raise ContractError.UNAUTHORIZED

    def _require_proposal_active(self, proposal_id: U64):
        dest = self.storage.get(f"prop_dest_{proposal_id}")
        if dest is None:
            raise ContractError.PROPOSAL_NOT_FOUND
        
        executed = self.storage.get(f"prop_executed_{proposal_id}", False)
        if executed:
            raise ContractError.ALREADY_EXECUTED

    def _get_now(self) -> U64:
        return self.env.ledger_timestamp()
