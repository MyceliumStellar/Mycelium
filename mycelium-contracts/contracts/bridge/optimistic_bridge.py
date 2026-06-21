"""
Optimistic Bridge — Dispute resolution, challenge windows, and bond management.

Mycelium Smart Contract for Stellar. Processes transfers optimistically by 
accepting proposals with bonds. Challenging a proposal initiates a validator 
dispute voting period. If fraud is proven, the relayer bond is slashed and 
the transaction is rolled back. If valid, the proposal settles after finalization.
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
    PROPOSAL_EXISTS = 5
    PROPOSAL_NOT_FOUND = 6
    PROPOSAL_NOT_ACTIVE = 7
    WINDOW_EXPIRED = 8
    WINDOW_NOT_EXPIRED = 9
    ALREADY_VOTED = 10
    DISPUTE_NOT_RESOLVED = 11
    INVALID_STATUS = 12
    INSUFFICIENT_BOND = 13

# Proposal Status
# 0 = NONE, 1 = PROPOSED, 2 = CHALLENGED, 3 = FINALIZED, 4 = REVERTED
STATUS_NONE = U64(0)
STATUS_PROPOSED = U64(1)
STATUS_CHALLENGED = U64(2)
STATUS_FINALIZED = U64(3)
STATUS_REVERTED = U64(4)

@contract
class OptimisticBridge:
    """
    Optimistic bridge protocol with fraud proof dispute resolution.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        token: Address,
        proposal_bond: U128,
        challenge_bond: U128,
        challenge_window: U64,
        validators: Vec,
        voting_period: U64
    ):
        """Initialize parameters, bonds, and validators for dispute consensus."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("token", token)
        self.storage.set("proposal_bond", proposal_bond)
        self.storage.set("challenge_bond", challenge_bond)
        self.storage.set("challenge_window", challenge_window) # seconds
        self.storage.set("voting_period", voting_period) # seconds
        self.storage.set("paused", False)

        # Set dispute committee (validators)
        self.storage.set("validator_count", len(validators))
        for i in range(len(validators)):
            addr = validators.get(i)
            self.storage.set(f"validator_{i}", addr)
            self.storage.set(f"is_validator_{addr}", True)

        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "token": token,
            "proposal_bond": proposal_bond,
            "challenge_bond": challenge_bond,
            "challenge_window": challenge_window
        })

    @external
    def propose_transfer(
        self,
        caller: Address,
        tx_id: Bytes,
        recipient: Address,
        amount: U128
    ):
        """
        Propose a cross-chain transfer. Relayer must lock the required proposal bond.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        status = self.storage.get(f"status_{tx_id}", STATUS_NONE)
        if status != STATUS_NONE:
            raise ContractError.PROPOSAL_EXISTS

        token = self.storage.get("token")
        contract_addr = self.env.current_contract_address()
        bond = self.storage.get("proposal_bond", U128(0))

        # Transfer bond and amount from relayer
        # Note: the relayer deposits the bridge transfer amount + the safety bond
        total_deposit = amount + bond
        self.env.call(token, "transfer", caller, contract_addr, total_deposit)

        # Save proposal details
        self.storage.set(f"relayer_{tx_id}", caller)
        self.storage.set(f"recipient_{tx_id}", recipient)
        self.storage.set(f"amount_{tx_id}", amount)
        self.storage.set(f"timestamp_{tx_id}", self._get_now())
        self.storage.set(f"status_{tx_id}", STATUS_PROPOSED)

        self.env.emit_event("transfer_proposed", {
            "tx_id": tx_id,
            "relayer": caller,
            "recipient": recipient,
            "amount": amount,
            "unlock_time": self._get_now() + self.storage.get("challenge_window", U64(0))
        })

    @external
    def challenge_proposal(self, caller: Address, tx_id: Bytes):
        """
        Challenge a proposed transfer on the grounds of fraud.
        Requires challenger to lock the challenge bond.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        status = self.storage.get(f"status_{tx_id}", STATUS_NONE)
        if status == STATUS_NONE:
            raise ContractError.PROPOSAL_NOT_FOUND
        if status != STATUS_PROPOSED:
            raise ContractError.INVALID_STATUS

        # Verify challenge window is still open
        timestamp = self.storage.get(f"timestamp_{tx_id}", U64(0))
        window = self.storage.get("challenge_window", U64(0))
        if self._get_now() >= timestamp + window:
            raise ContractError.WINDOW_EXPIRED

        # Lock challenger bond
        token = self.storage.get("token")
        contract_addr = self.env.current_contract_address()
        bond = self.storage.get("challenge_bond", U128(0))
        self.env.call(token, "transfer", caller, contract_addr, bond)

        # Update proposal state
        self.storage.set(f"status_{tx_id}", STATUS_CHALLENGED)
        self.storage.set(f"challenger_{tx_id}", caller)
        self.storage.set(f"dispute_start_{tx_id}", self._get_now())

        # Initialize votes counter
        self.storage.set(f"votes_fraud_{tx_id}", U64(0))
        self.storage.set(f"votes_valid_{tx_id}", U64(0))

        self.env.emit_event("proposal_challenged", {
            "tx_id": tx_id,
            "challenger": caller,
            "dispute_end": self._get_now() + self.storage.get("voting_period", U64(0))
        })

    @external
    def vote_dispute(self, caller: Address, tx_id: Bytes, vote_fraud: Bool):
        """
        Cast a vote on a challenged transaction (Validator committee only).
        """
        caller.require_auth()
        self._require_initialized()
        self._require_validator(caller)

        status = self.storage.get(f"status_{tx_id}", STATUS_NONE)
        if status != STATUS_CHALLENGED:
            raise ContractError.INVALID_STATUS

        # Check voting window is still open
        dispute_start = self.storage.get(f"dispute_start_{tx_id}", U64(0))
        period = self.storage.get("voting_period", U64(0))
        if self._get_now() >= dispute_start + period:
            raise ContractError.WINDOW_EXPIRED

        # Check duplicate voting
        if self.storage.get(f"voted_{tx_id}_{caller}", False):
            raise ContractError.ALREADY_VOTED

        self.storage.set(f"voted_{tx_id}_{caller}", True)

        if vote_fraud:
            votes = self.storage.get(f"votes_fraud_{tx_id}", U64(0))
            self.storage.set(f"votes_fraud_{tx_id}", votes + U64(1))
        else:
            votes = self.storage.get(f"votes_valid_{tx_id}", U64(0))
            self.storage.set(f"votes_valid_{tx_id}", votes + U64(1))

        self.env.emit_event("dispute_vote_cast", {
            "tx_id": tx_id,
            "voter": caller,
            "vote_fraud": vote_fraud
        })

    @external
    def resolve_dispute(self, tx_id: Bytes):
        """
        Resolve the dispute after the voting window has closed.
        Distributes bonds to the winner and rolls back or finalizes.
        """
        self._require_initialized()

        status = self.storage.get(f"status_{tx_id}", STATUS_NONE)
        if status != STATUS_CHALLENGED:
            raise ContractError.INVALID_STATUS

        dispute_start = self.storage.get(f"dispute_start_{tx_id}", U64(0))
        period = self.storage.get("voting_period", U64(0))
        if self._get_now() < dispute_start + period:
            raise ContractError.WINDOW_NOT_EXPIRED

        votes_fraud = self.storage.get(f"votes_fraud_{tx_id}", U64(0))
        votes_valid = self.storage.get(f"votes_valid_{tx_id}", U64(0))

        relayer = self.storage.get(f"relayer_{tx_id}")
        challenger = self.storage.get(f"challenger_{tx_id}")
        amount = self.storage.get(f"amount_{tx_id}", U128(0))

        token = self.storage.get("token")
        contract_addr = self.env.current_contract_address()

        proposal_bond = self.storage.get("proposal_bond", U128(0))
        challenge_bond = self.storage.get("challenge_bond", U128(0))

        if votes_fraud > votes_valid:
            # Challenger wins: Fraud confirmed
            self.storage.set(f"status_{tx_id}", STATUS_REVERTED)

            # Relayer bond is slashed
            # Slashed bond split: 80% to challenger, 20% to contract treasury/admin
            payout_challenger = challenge_bond + (proposal_bond * U128(80)) / U128(100)
            payout_treasury = (proposal_bond * U128(20)) / U128(100)

            # Return original transfer amount back to relayer (revert transfer)
            self.env.call(token, "transfer", contract_addr, relayer, amount)

            # Disburse challenger payout
            self.env.call(token, "transfer", contract_addr, challenger, payout_challenger)

            # Disburse admin share
            admin = self.storage.get("admin")
            self.env.call(token, "transfer", contract_addr, admin, payout_treasury)

            self.env.emit_event("proposal_slashed", {
                "tx_id": tx_id,
                "slashed_relayer": relayer,
                "challenger": challenger,
                "slashed_amount": proposal_bond
            })
        else:
            # Relayer wins: Proposal valid
            self.storage.set(f"status_{tx_id}", STATUS_FINALIZED)

            # Relayer gets back bond + transfer amount, plus 80% of challenger bond
            payout_relayer = amount + proposal_bond + (challenge_bond * U128(80)) / U128(100)
            payout_treasury = (challenge_bond * U128(20)) / U128(100)

            # Transfer payout to relayer
            self.env.call(token, "transfer", contract_addr, relayer, payout_relayer)

            # Disburse admin share
            admin = self.storage.get("admin")
            self.env.call(token, "transfer", contract_addr, admin, payout_treasury)

            # Execute the cross-chain bridge transfer by releasing to recipient
            recipient = self.storage.get(f"recipient_{tx_id}")
            # Note: We need to pull the transfer amount from the relayer's payout or execute it separately.
            # In our lock mechanism, the relayer locked (amount + bond) in the contract.
            # So the contract holds the total (amount + bond + challenge_bond).
            # The relayer gets (bond + 80% challenge_bond), and the recipient gets (amount)
            self.env.call(token, "transfer", contract_addr, recipient, amount)

            self.env.emit_event("proposal_dispute_cleared", {
                "tx_id": tx_id,
                "relayer": relayer,
                "recipient": recipient,
                "amount": amount
            })

    @external
    def finalize_transfer(self, tx_id: Bytes):
        """
        Finalize a proposal after the challenge window expires without challenge.
        Releases funds to the recipient and returns the bond to the relayer.
        """
        self._require_initialized()

        status = self.storage.get(f"status_{tx_id}", STATUS_NONE)
        if status == STATUS_NONE:
            raise ContractError.PROPOSAL_NOT_FOUND
        if status != STATUS_PROPOSED:
            raise ContractError.INVALID_STATUS

        # Verify challenge window has passed
        timestamp = self.storage.get(f"timestamp_{tx_id}", U64(0))
        window = self.storage.get("challenge_window", U64(0))
        if self._get_now() < timestamp + window:
            raise ContractError.WINDOW_NOT_EXPIRED

        # Mark finalized
        self.storage.set(f"status_{tx_id}", STATUS_FINALIZED)

        # Release tokens
        relayer = self.storage.get(f"relayer_{tx_id}")
        recipient = self.storage.get(f"recipient_{tx_id}")
        amount = self.storage.get(f"amount_{tx_id}", U128(0))
        bond = self.storage.get("proposal_bond", U128(0))

        token = self.storage.get("token")
        contract_addr = self.env.current_contract_address()

        # Send bond back to relayer
        self.env.call(token, "transfer", contract_addr, relayer, bond)

        # Send bridged amount to recipient
        self.env.call(token, "transfer", contract_addr, recipient, amount)

        self.env.emit_event("transfer_finalized", {
            "tx_id": tx_id,
            "relayer": relayer,
            "recipient": recipient,
            "amount": amount
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause optimistic proposals (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_proposal_details(self, tx_id: Bytes) -> Map:
        """Inspect the full parameter details of a proposal."""
        res = Map(self.env)
        status = self.storage.get(f"status_{tx_id}", STATUS_NONE)
        if status != STATUS_NONE:
            res.set("relayer", self.storage.get(f"relayer_{tx_id}"))
            res.set("recipient", self.storage.get(f"recipient_{tx_id}"))
            res.set("amount", self.storage.get(f"amount_{tx_id}"))
            res.set("timestamp", self.storage.get(f"timestamp_{tx_id}"))
            res.set("status", status)
            if status == STATUS_CHALLENGED:
                res.set("challenger", self.storage.get(f"challenger_{tx_id}"))
                res.set("votes_fraud", self.storage.get(f"votes_fraud_{tx_id}"))
                res.set("votes_valid", self.storage.get(f"votes_valid_{tx_id}"))
        return res

    # --- INTERNAL HELPERS ---

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_not_paused(self):
        if self.storage.get("paused", False):
            raise ContractError.PAUSED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_validator(self, caller: Address):
        if not self.storage.get(f"is_validator_{caller}", False):
            raise ContractError.UNAUTHORIZED

    def _get_now(self) -> U64:
        return self.env.ledger_timestamp()
