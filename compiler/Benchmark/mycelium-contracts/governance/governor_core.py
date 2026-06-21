"""
Governor Core — Full governance lifecycle with propose, vote, execute, and cancel.

Mycelium Smart Contract for Stellar

Implements a complete DAO governor with configurable voting periods, quorum
requirements, proposal thresholds, late quorum extension, and multi-action
proposal execution. Proposals traverse states: Pending → Active → Succeeded →
Queued → Executed, or may be Canceled / Defeated / Expired at various points.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    INVALID_PROPOSAL = 4
    PROPOSAL_NOT_FOUND = 5
    INVALID_STATE = 6
    ALREADY_VOTED = 7
    BELOW_PROPOSAL_THRESHOLD = 8
    QUORUM_NOT_REACHED = 9
    VOTING_NOT_STARTED = 10
    VOTING_ENDED = 11
    VOTING_NOT_ENDED = 12
    EMPTY_PROPOSAL = 13
    SELF_REFERENTIAL_EXECUTION = 14
    EXECUTION_FAILED = 15
    INVALID_VOTING_PERIOD = 16
    INVALID_QUORUM = 17
    INVALID_DELAY = 18
    PROPOSAL_EXPIRED = 19
    REENTRANT_CALL = 20
    ZERO_VOTING_POWER = 21
    INVALID_VOTE_TYPE = 22


class ProposalState:
    PENDING = 0
    ACTIVE = 1
    CANCELED = 2
    DEFEATED = 3
    SUCCEEDED = 4
    QUEUED = 5
    EXPIRED = 6
    EXECUTED = 7


class VoteType:
    AGAINST = 0
    FOR = 1
    ABSTAIN = 2


# Limits
MIN_VOTING_PERIOD = 3600          # 1 hour in seconds
MAX_VOTING_PERIOD = 2592000       # 30 days
MIN_VOTING_DELAY = 0              # immediate
MAX_VOTING_DELAY = 604800         # 7 days
MIN_QUORUM_BPS = 100              # 1%
MAX_QUORUM_BPS = 5000             # 50%
MAX_ACTIONS_PER_PROPOSAL = 20
LATE_QUORUM_EXTENSION = 7200      # 2 hours
GRACE_PERIOD = 1209600            # 14 days after succeeded


@contract
class GovernorCore:
    """Full-featured on-chain governor supporting propose, vote, execute,
    and cancel with quorum, proposal thresholds, and late quorum extension."""

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
        governance_token: Address,
        voting_delay: U64,
        voting_period: U64,
        quorum_bps: U64,
        proposal_threshold: U128,
    ):
        """Set up the governor with all governance parameters.

        Args:
            admin: The initial admin/guardian address.
            governance_token: Token whose balances determine voting power.
            voting_delay: Seconds between proposal creation and voting start.
            voting_period: Duration of the voting window in seconds.
            quorum_bps: Quorum as basis points of total supply (100 = 1%).
            proposal_threshold: Minimum token balance required to create a proposal.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        if voting_period < MIN_VOTING_PERIOD or voting_period > MAX_VOTING_PERIOD:
            raise ContractError.INVALID_VOTING_PERIOD
        if voting_delay < MIN_VOTING_DELAY or voting_delay > MAX_VOTING_DELAY:
            raise ContractError.INVALID_DELAY
        if quorum_bps < MIN_QUORUM_BPS or quorum_bps > MAX_QUORUM_BPS:
            raise ContractError.INVALID_QUORUM

        self.storage.set("admin", admin)
        self.storage.set("governance_token", governance_token)
        self.storage.set("voting_delay", voting_delay)
        self.storage.set("voting_period", voting_period)
        self.storage.set("quorum_bps", quorum_bps)
        self.storage.set("proposal_threshold", proposal_threshold)
        self.storage.set("proposal_count", U64(0))
        self.storage.set("execution_lock", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "governance_token": governance_token,
            "voting_delay": voting_delay,
            "voting_period": voting_period,
            "quorum_bps": quorum_bps,
            "proposal_threshold": proposal_threshold,
        })

    # ------------------------------------------------------------------ #
    #  Proposal creation                                                   #
    # ------------------------------------------------------------------ #

    @external
    def propose(
        self,
        proposer: Address,
        targets: Vec,
        values: Vec,
        calldatas: Vec,
        description: Symbol,
    ) -> U64:
        """Create a new governance proposal.

        Args:
            proposer: Address creating the proposal (must hold ≥ threshold tokens).
            targets: Contract addresses to call on execution.
            values: Native amounts sent with each call.
            calldatas: Encoded function calls for each target.
            description: Human-readable description / title.

        Returns:
            The newly created proposal ID.
        """
        self._require_initialized()
        proposer.require_auth()
        self._require_no_reentrant()

        if len(targets) == 0 or len(values) == 0 or len(calldatas) == 0:
            raise ContractError.EMPTY_PROPOSAL
        if len(targets) != len(values) or len(targets) != len(calldatas):
            raise ContractError.INVALID_PROPOSAL
        if len(targets) > MAX_ACTIONS_PER_PROPOSAL:
            raise ContractError.INVALID_PROPOSAL

        contract_addr = self.env.current_contract_address()
        for target in targets:
            if target == contract_addr:
                raise ContractError.SELF_REFERENTIAL_EXECUTION

        proposer_balance = self._get_voting_power(proposer)
        threshold = self.storage.get("proposal_threshold")
        if proposer_balance < threshold:
            raise ContractError.BELOW_PROPOSAL_THRESHOLD

        proposal_id = self.storage.get("proposal_count") + U64(1)
        self.storage.set("proposal_count", proposal_id)

        now = self.env.ledger().timestamp()
        voting_delay = self.storage.get("voting_delay")
        voting_period = self.storage.get("voting_period")
        vote_start = now + voting_delay
        vote_end = vote_start + voting_period

        total_supply = self._get_total_supply()
        quorum_bps = self.storage.get("quorum_bps")
        snapshot_quorum = (total_supply * U128(quorum_bps)) / U128(10000)

        proposal = {
            "id": proposal_id,
            "proposer": proposer,
            "targets": targets,
            "values": values,
            "calldatas": calldatas,
            "description": description,
            "vote_start": vote_start,
            "vote_end": vote_end,
            "snapshot_quorum": snapshot_quorum,
            "votes_for": U128(0),
            "votes_against": U128(0),
            "votes_abstain": U128(0),
            "canceled": False,
            "executed": False,
            "queued_at": U64(0),
        }

        self.storage.set(("proposal", proposal_id), proposal)
        self.storage.set(("proposal_voter_count", proposal_id), U64(0))

        self.env.emit_event("proposal_created", {
            "proposal_id": proposal_id,
            "proposer": proposer,
            "vote_start": vote_start,
            "vote_end": vote_end,
            "description": description,
            "action_count": U64(len(targets)),
        })

        return proposal_id

    # ------------------------------------------------------------------ #
    #  Voting                                                              #
    # ------------------------------------------------------------------ #

    @external
    def cast_vote(
        self,
        voter: Address,
        proposal_id: U64,
        vote_type: U64,
        reason: Symbol,
    ):
        """Cast a vote on an active proposal.

        Args:
            voter: The voting address (must hold governance tokens).
            proposal_id: The proposal to vote on.
            vote_type: 0 = Against, 1 = For, 2 = Abstain.
            reason: Optional reason string.
        """
        self._require_initialized()
        voter.require_auth()

        if vote_type > VoteType.ABSTAIN:
            raise ContractError.INVALID_VOTE_TYPE

        proposal = self._get_proposal(proposal_id)
        state = self._compute_state(proposal)
        if state != ProposalState.ACTIVE:
            raise ContractError.INVALID_STATE

        already_voted = self.storage.get(("vote", proposal_id, voter), False)
        if already_voted:
            raise ContractError.ALREADY_VOTED

        voting_power = self._get_voting_power(voter)
        if voting_power == U128(0):
            raise ContractError.ZERO_VOTING_POWER

        if vote_type == VoteType.FOR:
            proposal["votes_for"] = proposal["votes_for"] + voting_power
        elif vote_type == VoteType.AGAINST:
            proposal["votes_against"] = proposal["votes_against"] + voting_power
        else:
            proposal["votes_abstain"] = proposal["votes_abstain"] + voting_power

        now = self.env.ledger().timestamp()
        time_left = proposal["vote_end"] - now
        if time_left < LATE_QUORUM_EXTENSION:
            proposal["vote_end"] = now + LATE_QUORUM_EXTENSION

        self.storage.set(("proposal", proposal_id), proposal)
        self.storage.set(("vote", proposal_id, voter), True)
        self.storage.set(("vote_detail", proposal_id, voter), {
            "vote_type": vote_type,
            "voting_power": voting_power,
            "reason": reason,
            "timestamp": now,
        })

        voter_count = self.storage.get(("proposal_voter_count", proposal_id), U64(0))
        self.storage.set(("proposal_voter_count", proposal_id), voter_count + U64(1))

        self.env.emit_event("vote_cast", {
            "proposal_id": proposal_id,
            "voter": voter,
            "vote_type": vote_type,
            "voting_power": voting_power,
            "reason": reason,
        })

    # ------------------------------------------------------------------ #
    #  Queue & Execute                                                     #
    # ------------------------------------------------------------------ #

    @external
    def queue(self, caller: Address, proposal_id: U64):
        """Queue a succeeded proposal for execution after the grace period.

        Args:
            caller: Any address may queue a succeeded proposal.
            proposal_id: The proposal to queue.
        """
        self._require_initialized()
        caller.require_auth()

        proposal = self._get_proposal(proposal_id)
        state = self._compute_state(proposal)
        if state != ProposalState.SUCCEEDED:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        proposal["queued_at"] = now
        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("proposal_queued", {
            "proposal_id": proposal_id,
            "queued_at": now,
        })

    @external
    def execute(self, caller: Address, proposal_id: U64):
        """Execute a queued proposal's actions.

        Args:
            caller: Any address may trigger execution of a ready proposal.
            proposal_id: The proposal to execute.
        """
        self._require_initialized()
        caller.require_auth()
        self._require_no_reentrant()

        proposal = self._get_proposal(proposal_id)
        state = self._compute_state(proposal)
        if state != ProposalState.QUEUED:
            raise ContractError.INVALID_STATE

        self.storage.set("execution_lock", True)

        targets = proposal["targets"]
        values = proposal["values"]
        calldatas = proposal["calldatas"]

        for i in range(len(targets)):
            target = targets[i]
            value = values[i]
            calldata = calldatas[i]
            success = self.env.invoke_contract(target, calldata, value)
            if not success:
                self.storage.set("execution_lock", False)
                raise ContractError.EXECUTION_FAILED

        proposal["executed"] = True
        self.storage.set(("proposal", proposal_id), proposal)
        self.storage.set("execution_lock", False)

        self.env.emit_event("proposal_executed", {
            "proposal_id": proposal_id,
            "executor": caller,
        })

    # ------------------------------------------------------------------ #
    #  Cancel                                                              #
    # ------------------------------------------------------------------ #

    @external
    def cancel(self, caller: Address, proposal_id: U64):
        """Cancel a proposal. Only the proposer or admin may cancel, and only
        if the proposal has not already been executed.

        Args:
            caller: Must be the proposer or admin.
            proposal_id: The proposal to cancel.
        """
        self._require_initialized()
        caller.require_auth()

        proposal = self._get_proposal(proposal_id)
        state = self._compute_state(proposal)

        if state == ProposalState.EXECUTED:
            raise ContractError.INVALID_STATE
        if state == ProposalState.CANCELED:
            raise ContractError.INVALID_STATE

        admin = self.storage.get("admin")
        if caller != proposal["proposer"] and caller != admin:
            raise ContractError.UNAUTHORIZED

        proposal["canceled"] = True
        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("proposal_canceled", {
            "proposal_id": proposal_id,
            "canceled_by": caller,
        })

    # ------------------------------------------------------------------ #
    #  Admin functions                                                     #
    # ------------------------------------------------------------------ #

    @external
    def update_voting_period(self, admin: Address, new_period: U64):
        """Update the voting period duration. Only admin."""
        self._require_initialized()
        self._require_admin(admin)

        if new_period < MIN_VOTING_PERIOD or new_period > MAX_VOTING_PERIOD:
            raise ContractError.INVALID_VOTING_PERIOD

        self.storage.set("voting_period", new_period)
        self.env.emit_event("voting_period_updated", {"new_period": new_period})

    @external
    def update_quorum_bps(self, admin: Address, new_quorum_bps: U64):
        """Update quorum basis points. Only admin. Does not affect active proposals."""
        self._require_initialized()
        self._require_admin(admin)

        if new_quorum_bps < MIN_QUORUM_BPS or new_quorum_bps > MAX_QUORUM_BPS:
            raise ContractError.INVALID_QUORUM

        self.storage.set("quorum_bps", new_quorum_bps)
        self.env.emit_event("quorum_updated", {"new_quorum_bps": new_quorum_bps})

    @external
    def update_proposal_threshold(self, admin: Address, new_threshold: U128):
        """Update the minimum tokens needed to propose. Only admin."""
        self._require_initialized()
        self._require_admin(admin)

        self.storage.set("proposal_threshold", new_threshold)
        self.env.emit_event("proposal_threshold_updated", {"new_threshold": new_threshold})

    @external
    def update_voting_delay(self, admin: Address, new_delay: U64):
        """Update delay between proposal creation and vote start. Only admin."""
        self._require_initialized()
        self._require_admin(admin)

        if new_delay < MIN_VOTING_DELAY or new_delay > MAX_VOTING_DELAY:
            raise ContractError.INVALID_DELAY

        self.storage.set("voting_delay", new_delay)
        self.env.emit_event("voting_delay_updated", {"new_delay": new_delay})

    @external
    def transfer_admin(self, admin: Address, new_admin: Address):
        """Transfer the admin/guardian role. Only current admin."""
        self._require_initialized()
        self._require_admin(admin)

        self.storage.set("admin", new_admin)
        self.env.emit_event("admin_transferred", {
            "old_admin": admin,
            "new_admin": new_admin,
        })

    # ------------------------------------------------------------------ #
    #  View functions                                                      #
    # ------------------------------------------------------------------ #

    @view
    def get_proposal(self, proposal_id: U64) -> Map:
        """Return full proposal data including computed state."""
        proposal = self._get_proposal(proposal_id)
        proposal["state"] = self._compute_state(proposal)
        return proposal

    @view
    def get_proposal_state(self, proposal_id: U64) -> U64:
        """Return the current state enum value for a proposal."""
        proposal = self._get_proposal(proposal_id)
        return self._compute_state(proposal)

    @view
    def get_vote(self, proposal_id: U64, voter: Address) -> Map:
        """Return a voter's vote detail on a proposal."""
        self._get_proposal(proposal_id)
        detail = self.storage.get(("vote_detail", proposal_id, voter), None)
        if detail is None:
            return {"voted": False}
        detail["voted"] = True
        return detail

    @view
    def get_proposal_count(self) -> U64:
        """Return total number of proposals created."""
        return self.storage.get("proposal_count", U64(0))

    @view
    def get_voter_count(self, proposal_id: U64) -> U64:
        """Return the number of unique voters on a proposal."""
        return self.storage.get(("proposal_voter_count", proposal_id), U64(0))

    @view
    def get_quorum(self) -> U128:
        """Return the current quorum token amount based on total supply."""
        total_supply = self._get_total_supply()
        quorum_bps = self.storage.get("quorum_bps")
        return (total_supply * U128(quorum_bps)) / U128(10000)

    @view
    def get_governance_params(self) -> Map:
        """Return all governance parameters."""
        return {
            "voting_delay": self.storage.get("voting_delay"),
            "voting_period": self.storage.get("voting_period"),
            "quorum_bps": self.storage.get("quorum_bps"),
            "proposal_threshold": self.storage.get("proposal_threshold"),
            "governance_token": self.storage.get("governance_token"),
            "admin": self.storage.get("admin"),
        }

    @view
    def has_voted(self, proposal_id: U64, voter: Address) -> Bool:
        """Check whether a specific address has voted on a proposal."""
        return self.storage.get(("vote", proposal_id, voter), False)

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        caller.require_auth()
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_no_reentrant(self):
        if self.storage.get("execution_lock", False):
            raise ContractError.REENTRANT_CALL

    def _get_proposal(self, proposal_id: U64) -> Map:
        proposal = self.storage.get(("proposal", proposal_id), None)
        if proposal is None:
            raise ContractError.PROPOSAL_NOT_FOUND
        return proposal

    def _compute_state(self, proposal: Map) -> U64:
        """Derive the current state of a proposal from stored flags and timestamps."""
        if proposal["canceled"]:
            return ProposalState.CANCELED
        if proposal["executed"]:
            return ProposalState.EXECUTED

        now = self.env.ledger().timestamp()

        if now < proposal["vote_start"]:
            return ProposalState.PENDING

        if now <= proposal["vote_end"]:
            return ProposalState.ACTIVE

        total_participating = (
            proposal["votes_for"]
            + proposal["votes_against"]
            + proposal["votes_abstain"]
        )
        quorum_met = total_participating >= proposal["snapshot_quorum"]
        vote_passed = proposal["votes_for"] > proposal["votes_against"]

        if not quorum_met or not vote_passed:
            return ProposalState.DEFEATED

        if proposal["queued_at"] > U64(0):
            elapsed_since_queue = now - proposal["queued_at"]
            if elapsed_since_queue > GRACE_PERIOD:
                return ProposalState.EXPIRED
            return ProposalState.QUEUED

        elapsed_since_end = now - proposal["vote_end"]
        if elapsed_since_end > GRACE_PERIOD:
            return ProposalState.EXPIRED

        return ProposalState.SUCCEEDED

    def _get_voting_power(self, voter: Address) -> U128:
        """Query the governance token contract for the voter's balance."""
        token = self.storage.get("governance_token")
        balance = self.env.invoke_contract(token, "balance", [voter])
        return balance

    def _get_total_supply(self) -> U128:
        """Query the governance token contract for total supply."""
        token = self.storage.get("governance_token")
        return self.env.invoke_contract(token, "total_supply", [])
