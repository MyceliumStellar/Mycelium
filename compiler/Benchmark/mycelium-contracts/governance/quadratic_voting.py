"""
Quadratic Voting — Identity-linked credit allocation, quadratic vote costs (N^2), and incremental vote casting.

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
    PROPOSAL_NOT_FOUND = 4
    IDENTITY_NOT_VERIFIED = 5
    INSUFFICIENT_CREDITS = 6
    VOTING_ENDED = 7
    INVALID_STATE = 8
    VOTING_NOT_ENDED = 9
    ZERO_VOTES = 10


class ProposalState:
    ACTIVE = 0
    DEFEATED = 1
    SUCCEEDED = 2
    EXECUTED = 3


@contract
class QuadraticVoting:
    """A governance contract supporting quadratic voting cost calculations and identity verification."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        identity_registry: Address,
        initial_credits: U64,
        voting_duration: U64,
    ):
        """Initialize the Quadratic Voting contract.

        Args:
            admin: Admin address.
            identity_registry: Address of the Identity/Sybil registry contract.
            initial_credits: Number of voting credits allocated to each identity per proposal.
            voting_duration: Duration of voting period in seconds.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("identity_registry", identity_registry)
        self.storage.set("initial_credits", initial_credits)
        self.storage.set("voting_duration", voting_duration)
        self.storage.set("proposal_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "identity_registry": identity_registry,
            "initial_credits": initial_credits,
        })

    @external
    def propose(self, proposer: Address, target: Address, calldata: Bytes, description: Symbol) -> U64:
        """Submit a new proposal. Proposer must be verified.

        Args:
            proposer: Address submitting proposal.
            target: Execution target contract.
            calldata: Execution calldata.
            description: Description symbol.
        """
        self._require_initialized()
        proposer.require_auth()

        # Check identity verification
        if not self._is_verified(proposer):
            raise ContractError.IDENTITY_NOT_VERIFIED

        proposal_id = self.storage.get("proposal_count") + U64(1)
        self.storage.set("proposal_count", proposal_id)

        now = self.env.ledger().timestamp()
        vote_end = now + self.storage.get("voting_duration")

        proposal = {
            "id": proposal_id,
            "proposer": proposer,
            "target": target,
            "calldata": calldata,
            "description": description,
            "vote_end": vote_end,
            "votes_for": U64(0),
            "votes_against": U64(0),
            "executed": False,
        }

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("proposal_created", {
            "proposal_id": proposal_id,
            "proposer": proposer,
            "target": target,
            "vote_end": vote_end,
        })

        return proposal_id

    @external
    def cast_quadratic_vote(
        self,
        voter: Address,
        proposal_id: U64,
        support: Bool,
        desired_votes: U64,
    ):
        """Cast votes using quadratic credits. Support incremental top-up.

        Credit cost = desired_votes^2.
        Voter pays the difference: (desired_votes^2) - (current_votes^2).

        Args:
            voter: Verified voter address.
            proposal_id: Target proposal ID.
            support: True for FOR, False for AGAINST.
            desired_votes: The total cumulative votes the voter wishes to cast.
        """
        self._require_initialized()
        voter.require_auth()

        # Check identity verification
        if not self._is_verified(voter):
            raise ContractError.IDENTITY_NOT_VERIFIED

        proposal = self._get_proposal(proposal_id)
        if self.env.ledger().timestamp() >= proposal["vote_end"]:
            raise ContractError.VOTING_ENDED

        # Get existing votes cast by this voter on this proposal
        current_votes = self.storage.get(("voter_votes", proposal_id, voter), U64(0))
        if desired_votes <= current_votes:
            raise ContractError.ZERO_VOTES

        # Calculate credit costs: Cost = V^2
        current_cost = current_votes * current_votes
        new_cost = desired_votes * desired_votes
        credit_cost_diff = new_cost - current_cost

        # Track remaining credits
        credits_spent = self.storage.get(("voter_credits_spent", proposal_id, voter), U64(0))
        initial_credits = self.storage.get("initial_credits")
        remaining_credits = initial_credits - credits_spent

        if remaining_credits < credit_cost_diff:
            raise ContractError.INSUFFICIENT_CREDITS

        # Deduct credits and update votes
        self.storage.set(("voter_credits_spent", proposal_id, voter), credits_spent + credit_cost_diff)
        self.storage.set(("voter_votes", proposal_id, voter), desired_votes)

        votes_diff = desired_votes - current_votes

        # Tally vote totals
        # In a real setup, if voter switches support, it's complex, so we assume consistent support direction
        # or require they only vote in one direction. We store their vote direction:
        existing_support = self.storage.get(("voter_support", proposal_id, voter), None)
        if existing_support is not None and existing_support != support:
            # Cannot switch support direction on incremental top-up to avoid double spending logic complexities
            raise ContractError.INVALID_STATE
        self.storage.set(("voter_support", proposal_id, voter), support)

        if support:
            proposal["votes_for"] = proposal["votes_for"] + votes_diff
        else:
            proposal["votes_against"] = proposal["votes_against"] + votes_diff

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("vote_cast", {
            "proposal_id": proposal_id,
            "voter": voter,
            "votes_added": votes_diff,
            "credits_spent": credit_cost_diff,
        })

    @external
    def execute_proposal(self, executor: Address, proposal_id: U64):
        """Execute proposal if voting period has ended and it passed.

        Args:
            executor: Trigger address.
            proposal_id: Proposal ID.
        """
        self._require_initialized()
        executor.require_auth()

        proposal = self._get_proposal(proposal_id)
        if proposal["executed"]:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        if now < proposal["vote_end"]:
            raise ContractError.VOTING_NOT_ENDED

        passed = proposal["votes_for"] > proposal["votes_against"]
        if not passed:
            raise ContractError.INVALID_STATE

        proposal["executed"] = True
        self.storage.set(("proposal", proposal_id), proposal)

        # Trigger execution
        success = self.env.invoke_contract(proposal["target"], "execute", [proposal["calldata"]])
        if not success:
            proposal["executed"] = False
            self.storage.set(("proposal", proposal_id), proposal)
            raise ContractError.INVALID_STATE

        self.env.emit_event("proposal_executed", {
            "proposal_id": proposal_id,
            "executor": executor,
        })

    @external
    def transfer_credits(self, sender: Address, recipient: Address, proposal_id: U64, amount: U64):
        """Redistribution: verified voters can transfer credits to each other for a proposal.

        Args:
            sender: Verifying sender address.
            recipient: Verifying recipient address.
            proposal_id: Target proposal ID.
            amount: Credits to transfer.
        """
        self._require_initialized()
        sender.require_auth()

        if not self._is_verified(sender) or not self._is_verified(recipient):
            raise ContractError.IDENTITY_NOT_VERIFIED

        proposal = self._get_proposal(proposal_id)
        if self.env.ledger().timestamp() >= proposal["vote_end"]:
            raise ContractError.VOTING_ENDED

        # Calculate sender's remaining credits
        sender_spent = self.storage.get(("voter_credits_spent", proposal_id, sender), U64(0))
        initial_credits = self.storage.get("initial_credits")
        sender_remaining = initial_credits - sender_spent

        if sender_remaining < amount:
            raise ContractError.INSUFFICIENT_CREDITS

        # Update spent counts: sender spent increases (credits vanish from their view),
        # but recipient spent decreases (recipient gains credits)
        self.storage.set(("voter_credits_spent", proposal_id, sender), sender_spent + amount)

        recipient_spent = self.storage.get(("voter_credits_spent", proposal_id, recipient), U64(0))
        # If recipient has not spent much, they can go negative on spent (i.e. remaining > initial_credits)
        # We model this by subtracting from recipient's spent amount:
        if recipient_spent >= amount:
            self.storage.set(("voter_credits_spent", proposal_id, recipient), recipient_spent - amount)
        else:
            # If recipient spent is 0, we can credit them by offsetting their credit limit
            # We track extra credits granted:
            extra = self.storage.get(("voter_extra_credits", proposal_id, recipient), U64(0))
            self.storage.set(("voter_extra_credits", proposal_id, recipient), extra + (amount - recipient_spent))
            self.storage.set(("voter_credits_spent", proposal_id, recipient), U64(0))

        self.env.emit_event("credits_transferred", {
            "proposal_id": proposal_id,
            "sender": sender,
            "recipient": recipient,
            "amount": amount,
        })

    @view
    def get_voter_state(self, proposal_id: U64, voter: Address) -> Map:
        """Get remaining credits and votes cast by voter."""
        spent = self.storage.get(("voter_credits_spent", proposal_id, voter), U64(0))
        extra = self.storage.get(("voter_extra_credits", proposal_id, voter), U64(0))
        initial = self.storage.get("initial_credits")
        remaining = (initial + extra) - spent

        return {
            "votes_cast": self.storage.get(("voter_votes", proposal_id, voter), U64(0)),
            "credits_spent": spent,
            "credits_remaining": remaining,
            "support": self.storage.get(("voter_support", proposal_id, voter), None),
        }

    @view
    def get_proposal(self, proposal_id: U64) -> Map:
        """Get proposal details."""
        return self._get_proposal(proposal_id)

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _get_proposal(self, proposal_id: U64) -> Map:
        proposal = self.storage.get(("proposal", proposal_id), None)
        if proposal is None:
            raise ContractError.PROPOSAL_NOT_FOUND
        return proposal

    def _is_verified(self, voter: Address) -> Bool:
        registry = self.storage.get("identity_registry")
        # Call verification check on registry
        return self.env.invoke_contract(registry, "is_verified", [voter])
