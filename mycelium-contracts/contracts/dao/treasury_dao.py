"""
Treasury DAO — Quorum-based spending proposals with veto guardian and execution safety checks.

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
    INVALID_PROPOSAL = 4
    PROPOSAL_NOT_FOUND = 5
    INVALID_STATE = 6
    ALREADY_VOTED = 7
    INSUFFICIENT_FUNDS = 8
    INSUFFICIENT_VOTING_POWER = 9
    VOTING_ENDED = 10
    VOTING_NOT_ENDED = 11
    REENTRANT_CALL = 12
    INVALID_DURATION = 13
    INVALID_QUORUM = 14
    PROPOSAL_VETOED = 15


class ProposalState:
    PENDING = 0
    ACTIVE = 1
    VETOED = 2
    DEFEATED = 3
    SUCCEEDED = 4
    EXECUTED = 5


class VoteType:
    AGAINST = 0
    FOR = 1


@contract
class TreasuryDAO:
    """A DAO contract designed to manage treasury spends with token snapshot weights and veto guardians."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        token: Address,
        guardian: Address,
        proposal_threshold: U128,
        quorum_bps: U64,
        min_voting_duration: U64,
    ):
        """Initialize the Treasury DAO contract parameters.

        Args:
            admin: Admin address with parameters update rights.
            token: Voting token address.
            guardian: Veto guardian address.
            proposal_threshold: Minimum balance required to propose.
            quorum_bps: Quorum in basis points (100 = 1%).
            min_voting_duration: Minimum voting period duration in seconds.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        if quorum_bps > U64(10000) or quorum_bps == U64(0):
            raise ContractError.INVALID_QUORUM
        if min_voting_duration == U64(0):
            raise ContractError.INVALID_DURATION

        self.storage.set("admin", admin)
        self.storage.set("token", token)
        self.storage.set("guardian", guardian)
        self.storage.set("proposal_threshold", proposal_threshold)
        self.storage.set("quorum_bps", quorum_bps)
        self.storage.set("min_voting_duration", min_voting_duration)
        self.storage.set("proposal_count", U64(0))
        self.storage.set("execution_lock", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "token": token,
            "guardian": guardian,
            "proposal_threshold": proposal_threshold,
            "quorum_bps": quorum_bps,
            "min_voting_duration": min_voting_duration,
        })

    @external
    def propose(
        self,
        proposer: Address,
        recipient: Address,
        amount: U128,
        description: Symbol,
        duration: U64,
    ) -> U64:
        """Submit a new treasury spend proposal.

        Args:
            proposer: Address of the proposal creator.
            recipient: Address that receives the funds if proposal passes.
            amount: Amount of native/token funds to spend.
            description: Symbol text describing proposal.
            duration: Voting duration in seconds.
        """
        self._require_initialized()
        proposer.require_auth()
        self._require_no_reentrant()

        if amount == U128(0):
            raise ContractError.INVALID_PROPOSAL

        min_duration = self.storage.get("min_voting_duration")
        if duration < min_duration:
            raise ContractError.INVALID_DURATION

        proposer_power = self._get_voting_power(proposer)
        threshold = self.storage.get("proposal_threshold")
        if proposer_power < threshold:
            raise ContractError.INSUFFICIENT_VOTING_POWER

        proposal_id = self.storage.get("proposal_count") + U64(1)
        self.storage.set("proposal_count", proposal_id)

        now = self.env.ledger().timestamp()
        vote_end = now + duration

        total_supply = self._get_total_supply()
        quorum_bps = self.storage.get("quorum_bps")
        required_quorum = (total_supply * U128(quorum_bps)) / U128(10000)

        proposal = {
            "id": proposal_id,
            "proposer": proposer,
            "recipient": recipient,
            "amount": amount,
            "description": description,
            "vote_end": vote_end,
            "required_quorum": required_quorum,
            "votes_for": U128(0),
            "votes_against": U128(0),
            "executed": False,
            "vetoed": False,
        }

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("proposal_created", {
            "proposal_id": proposal_id,
            "proposer": proposer,
            "recipient": recipient,
            "amount": amount,
            "vote_end": vote_end,
        })

        return proposal_id

    @external
    def cast_vote(self, voter: Address, proposal_id: U64, vote_type: U64):
        """Cast a yes/no vote on a proposal.

        Args:
            voter: The voting token holder address.
            proposal_id: ID of the proposal.
            vote_type: 0 for AGAINST, 1 for FOR.
        """
        self._require_initialized()
        voter.require_auth()

        proposal = self._get_proposal(proposal_id)
        state = self._compute_state(proposal)
        if state != ProposalState.ACTIVE:
            raise ContractError.INVALID_STATE

        already_voted = self.storage.get(("voted", proposal_id, voter), False)
        if already_voted:
            raise ContractError.ALREADY_VOTED

        voting_power = self._get_voting_power(voter)
        if voting_power == U128(0):
            raise ContractError.INSUFFICIENT_VOTING_POWER

        if vote_type == VoteType.FOR:
            proposal["votes_for"] = proposal["votes_for"] + voting_power
        elif vote_type == VoteType.AGAINST:
            proposal["votes_against"] = proposal["votes_against"] + voting_power
        else:
            raise ContractError.INVALID_STATE

        self.storage.set(("proposal", proposal_id), proposal)
        self.storage.set(("voted", proposal_id, voter), True)

        self.env.emit_event("vote_cast", {
            "proposal_id": proposal_id,
            "voter": voter,
            "vote_type": vote_type,
            "weight": voting_power,
        })

    @external
    def veto(self, guardian: Address, proposal_id: U64):
        """Veto a proposal to prevent any execution. Only guardian.

        Args:
            guardian: The registered veto guardian.
            proposal_id: The proposal to veto.
        """
        self._require_initialized()
        guardian.require_auth()

        registered_guardian = self.storage.get("guardian")
        if guardian != registered_guardian:
            raise ContractError.UNAUTHORIZED

        proposal = self._get_proposal(proposal_id)
        if proposal["executed"] or proposal["vetoed"]:
            raise ContractError.INVALID_STATE

        proposal["vetoed"] = True
        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("proposal_vetoed", {
            "proposal_id": proposal_id,
            "guardian": guardian,
        })

    @external
    def execute(self, executor: Address, proposal_id: U64):
        """Execute a passed treasury spend proposal.

        Args:
            executor: Caller triggering execution.
            proposal_id: ID of the proposal.
        """
        self._require_initialized()
        executor.require_auth()
        self._require_no_reentrant()

        proposal = self._get_proposal(proposal_id)
        state = self._compute_state(proposal)
        if state != ProposalState.SUCCEEDED:
            raise ContractError.INVALID_STATE

        self.storage.set("execution_lock", True)

        # Execution safety checks: balance checking
        token = self.storage.get("token")
        treasury_balance = self.env.invoke_contract(token, "balance", [self.env.current_contract_address()])
        if treasury_balance < proposal["amount"]:
            self.storage.set("execution_lock", False)
            raise ContractError.INSUFFICIENT_FUNDS

        # Execute spending transfer
        transfer_args = [self.env.current_contract_address(), proposal["recipient"], proposal["amount"]]
        success = self.env.invoke_contract(token, "transfer", transfer_args)
        if not success:
            self.storage.set("execution_lock", False)
            raise ContractError.INSUFFICIENT_FUNDS

        proposal["executed"] = True
        self.storage.set(("proposal", proposal_id), proposal)
        self.storage.set("execution_lock", False)

        self.env.emit_event("proposal_executed", {
            "proposal_id": proposal_id,
            "executor": executor,
            "amount": proposal["amount"],
            "recipient": proposal["recipient"],
        })

    @external
    def update_guardian(self, admin: Address, new_guardian: Address):
        """Change the veto guardian address. Only admin."""
        self._require_initialized()
        self._require_admin(admin)

        self.storage.set("guardian", new_guardian)
        self.env.emit_event("guardian_updated", {"new_guardian": new_guardian})

    @external
    def update_params(
        self,
        admin: Address,
        new_threshold: U128,
        new_quorum: U64,
        new_duration: U64,
    ):
        """Update contract parameters. Only admin."""
        self._require_initialized()
        self._require_admin(admin)

        if new_quorum > U64(10000) or new_quorum == U64(0):
            raise ContractError.INVALID_QUORUM
        if new_duration == U64(0):
            raise ContractError.INVALID_DURATION

        self.storage.set("proposal_threshold", new_threshold)
        self.storage.set("quorum_bps", new_quorum)
        self.storage.set("min_voting_duration", new_duration)

        self.env.emit_event("params_updated", {
            "proposal_threshold": new_threshold,
            "quorum_bps": new_quorum,
            "min_voting_duration": new_duration,
        })

    @view
    def get_proposal(self, proposal_id: U64) -> Map:
        """Get the full proposal details."""
        proposal = self._get_proposal(proposal_id)
        proposal["state"] = self._compute_state(proposal)
        return proposal

    @view
    def get_proposal_state(self, proposal_id: U64) -> U64:
        """Get the current state enum of a proposal."""
        proposal = self._get_proposal(proposal_id)
        return self._compute_state(proposal)

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
        if proposal["vetoed"]:
            return ProposalState.VETOED
        if proposal["executed"]:
            return ProposalState.EXECUTED

        now = self.env.ledger().timestamp()
        if now < proposal["vote_end"]:
            return ProposalState.ACTIVE

        total_votes = proposal["votes_for"] + proposal["votes_against"]
        if total_votes < proposal["required_quorum"]:
            return ProposalState.DEFEATED

        if proposal["votes_for"] > proposal["votes_against"]:
            return ProposalState.SUCCEEDED
        else:
            return ProposalState.DEFEATED

    def _get_voting_power(self, voter: Address) -> U128:
        token = self.storage.get("token")
        return self.env.invoke_contract(token, "balance", [voter])

    def _get_total_supply(self) -> U128:
        token = self.storage.get("token")
        return self.env.invoke_contract(token, "total_supply", [])
