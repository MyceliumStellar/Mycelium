"""
Optimistic Governance — Governance system based on optimistic execution with challenge period.

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
    INVALID_CHALLENGE_PERIOD = 4
    INVALID_VOTING_PERIOD = 5
    INVALID_QUORUM = 6
    INVALID_PROPOSAL = 7
    PROPOSAL_NOT_FOUND = 8
    INVALID_STATE = 9
    CHALLENGE_PERIOD_ACTIVE = 10
    CHALLENGE_PERIOD_EXPIRED = 11
    ALREADY_VOTED = 12
    VOTING_NOT_ENDED = 13
    VOTING_ENDED = 14
    ZERO_VOTING_POWER = 15
    INSUFFICIENT_BOND = 16
    TRANSFER_FAILED = 17
    EXECUTION_FAILED = 18
    SELF_REFERENTIAL_EXECUTION = 19
    REENTRANT_CALL = 20
    ZERO_ADDRESS = 21


class ProposalState:
    SUBMITTED = 0
    CHALLENGED = 1
    APPROVED = 2
    VETOED = 3
    EXECUTED = 4
    CANCELED = 5


# Constraints
MIN_CHALLENGE_PERIOD = 259200      # 3 days in seconds
MAX_CHALLENGE_PERIOD = 1209600     # 14 days in seconds
MIN_VOTING_PERIOD = 86400          # 1 day in seconds
MAX_VOTING_PERIOD = 604800         # 7 days in seconds
MIN_QUORUM_BPS = 100               # 1%
MAX_QUORUM_BPS = 10000             # 100%
MAX_ACTIONS = 10


@contract
class OptimisticGovernance:
    """Optimistic governance contract allowing proposals to execute automatically
    unless challenged by users posting a challenger bond, triggering a vote."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        bond_token: Address,
        governance_token: Address,
        challenge_period: U64,
        voting_period: U64,
        proposer_bond: U128,
        challenger_bond: U128,
        quorum_bps: U64,
    ):
        """Initialize the Optimistic Governance contract configurations."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        if challenge_period < MIN_CHALLENGE_PERIOD or challenge_period > MAX_CHALLENGE_PERIOD:
            raise ContractError.INVALID_CHALLENGE_PERIOD
        if voting_period < MIN_VOTING_PERIOD or voting_period > MAX_VOTING_PERIOD:
            raise ContractError.INVALID_VOTING_PERIOD
        if quorum_bps < MIN_QUORUM_BPS or quorum_bps > MAX_QUORUM_BPS:
            raise ContractError.INVALID_QUORUM

        self.storage.set("admin", admin)
        self.storage.set("bond_token", bond_token)
        self.storage.set("governance_token", governance_token)
        self.storage.set("challenge_period", challenge_period)
        self.storage.set("voting_period", voting_period)
        self.storage.set("proposer_bond", proposer_bond)
        self.storage.set("challenger_bond", challenger_bond)
        self.storage.set("quorum_bps", quorum_bps)
        self.storage.set("proposal_count", U64(0))
        self.storage.set("execution_lock", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "bond_token": bond_token,
            "governance_token": governance_token,
            "challenge_period": challenge_period,
            "proposer_bond": proposer_bond,
            "challenger_bond": challenger_bond,
        })

    @external
    def propose(
        self,
        proposer: Address,
        targets: Vec,
        values: Vec,
        calldatas: Vec,
        description: Symbol,
    ) -> U64:
        """Create a new proposal and deposit the required proposer bond.

        Args:
            proposer: The address creating the proposal.
            targets: Vector of target contract addresses to call on execution.
            values: Vector of native token values for each call.
            calldatas: Vector of bytes calldata for each call.
            description: Description symbol.
        """
        self._require_initialized()
        proposer.require_auth()
        self._require_no_reentrant()

        if len(targets) == 0 or len(targets) > MAX_ACTIONS:
            raise ContractError.INVALID_PROPOSAL
        if len(targets) != len(values) or len(targets) != len(calldatas):
            raise ContractError.INVALID_PROPOSAL

        contract_addr = self.env.current_contract_address()
        for target in targets:
            if target == contract_addr:
                raise ContractError.SELF_REFERENTIAL_EXECUTION

        # Transfer proposer bond to the contract
        bond_token = self.storage.get("bond_token")
        proposer_bond = self.storage.get("proposer_bond")
        if proposer_bond > U128(0):
            success = self.env.invoke_contract(bond_token, "transfer", [proposer, contract_addr, proposer_bond])
            if not success:
                raise ContractError.TRANSFER_FAILED

        proposal_id = self.storage.get("proposal_count") + U64(1)
        self.storage.set("proposal_count", proposal_id)

        now = self.env.ledger().timestamp()
        challenge_period = self.storage.get("challenge_period")
        challenge_end = now + challenge_period

        proposal = {
            "id": proposal_id,
            "proposer": proposer,
            "targets": targets,
            "values": values,
            "calldatas": calldatas,
            "description": description,
            "submitted_at": now,
            "challenge_end": challenge_end,
            "status": ProposalState.SUBMITTED,
            "proposer_bond": proposer_bond,
            "challenger": proposer,  # Placeholder, not challenged yet
            "challenger_bond": U128(0),
            "vote_end": U64(0),
            "votes_for": U128(0),
            "votes_against": U128(0),
        }

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("proposal_submitted", {
            "proposal_id": proposal_id,
            "proposer": proposer,
            "challenge_end": challenge_end,
            "proposer_bond": proposer_bond,
        })

        return proposal_id

    @external
    def challenge(self, challenger: Address, proposal_id: U64):
        """Challenge an active proposal during the challenge period.

        Args:
            challenger: The address challenging the proposal.
            proposal_id: The ID of the proposal to challenge.
        """
        self._require_initialized()
        challenger.require_auth()
        self._require_no_reentrant()

        proposal = self._get_proposal(proposal_id)
        if proposal["status"] != ProposalState.SUBMITTED:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        if now > proposal["challenge_end"]:
            raise ContractError.CHALLENGE_PERIOD_EXPIRED

        # Transfer challenger bond to contract
        bond_token = self.storage.get("bond_token")
        challenger_bond = self.storage.get("challenger_bond")
        contract_addr = self.env.current_contract_address()
        if challenger_bond > U128(0):
            success = self.env.invoke_contract(bond_token, "transfer", [challenger, contract_addr, challenger_bond])
            if not success:
                raise ContractError.TRANSFER_FAILED

        voting_period = self.storage.get("voting_period")
        vote_end = now + voting_period

        proposal["status"] = ProposalState.CHALLENGED
        proposal["challenger"] = challenger
        proposal["challenger_bond"] = challenger_bond
        proposal["vote_end"] = vote_end

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("proposal_challenged", {
            "proposal_id": proposal_id,
            "challenger": challenger,
            "vote_end": vote_end,
            "challenger_bond": challenger_bond,
        })

    @external
    def cast_vote(self, voter: Address, proposal_id: U64, vote_for: Bool):
        """Cast a vote on a challenged proposal to resolve the dispute.

        Args:
            voter: The voter address.
            proposal_id: The challenged proposal ID.
            vote_for: True to vote in favor of the proposal, False to vote against (for challenger).
        """
        self._require_initialized()
        voter.require_auth()

        proposal = self._get_proposal(proposal_id)
        if proposal["status"] != ProposalState.CHALLENGED:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        if now > proposal["vote_end"]:
            raise ContractError.VOTING_ENDED

        already_voted = self.storage.get(("voted", proposal_id, voter), False)
        if already_voted:
            raise ContractError.ALREADY_VOTED

        voting_power = self._get_voting_power(voter)
        if voting_power == U128(0):
            raise ContractError.ZERO_VOTING_POWER

        if vote_for:
            proposal["votes_for"] = proposal["votes_for"] + voting_power
        else:
            proposal["votes_against"] = proposal["votes_against"] + voting_power

        self.storage.set(("voted", proposal_id, voter), True)
        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("vote_cast", {
            "proposal_id": proposal_id,
            "voter": voter,
            "vote_for": vote_for,
            "voting_power": voting_power,
        })

    @external
    def resolve_challenge(self, caller: Address, proposal_id: U64):
        """Resolve a challenged proposal after the voting period has ended.

        Args:
            caller: Any address can trigger resolution.
            proposal_id: The proposal ID.
        """
        self._require_initialized()
        caller.require_auth()
        self._require_no_reentrant()

        proposal = self._get_proposal(proposal_id)
        if proposal["status"] != ProposalState.CHALLENGED:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        if now <= proposal["vote_end"]:
            raise ContractError.VOTING_NOT_ENDED

        # Calculate outcomes
        votes_for = proposal["votes_for"]
        votes_against = proposal["votes_against"]
        total_votes = votes_for + votes_against

        # Quorum check
        total_supply = self._get_total_supply()
        quorum_bps = self.storage.get("quorum_bps")
        required_quorum = (total_supply * U128(quorum_bps)) / U128(10000)

        bond_token = self.storage.get("bond_token")
        contract_addr = self.env.current_contract_address()

        proposal_passed = (total_votes >= required_quorum) and (votes_for > votes_against)

        if proposal_passed:
            # Proposal passes!
            proposal["status"] = ProposalState.APPROVED
            
            # Slashing: challenger bond goes to the proposer
            proposer = proposal["proposer"]
            challenger_bond = proposal["challenger_bond"]
            proposer_bond = proposal["proposer_bond"]

            # Refund proposer bond
            if proposer_bond > U128(0):
                self.env.invoke_contract(bond_token, "transfer", [contract_addr, proposer, proposer_bond])

            # Pay slashed bond to proposer
            if challenger_bond > U128(0):
                self.env.invoke_contract(bond_token, "transfer", [contract_addr, proposer, challenger_bond])
        else:
            # Proposal failed or quorum not met!
            proposal["status"] = ProposalState.VETOED
            
            # Slashing: proposer bond goes to the challenger
            challenger = proposal["challenger"]
            proposer_bond = proposal["proposer_bond"]
            challenger_bond = proposal["challenger_bond"]

            # Refund challenger bond
            if challenger_bond > U128(0):
                self.env.invoke_contract(bond_token, "transfer", [contract_addr, challenger, challenger_bond])

            # Pay slashed bond to challenger
            if proposer_bond > U128(0):
                self.env.invoke_contract(bond_token, "transfer", [contract_addr, challenger, proposer_bond])

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("challenge_resolved", {
            "proposal_id": proposal_id,
            "passed": proposal_passed,
            "votes_for": votes_for,
            "votes_against": votes_against,
        })

    @external
    def execute(self, executor: Address, proposal_id: U64):
        """Execute an approved proposal or a proposal that survived the challenge period.

        Args:
            executor: Any address may execute.
            proposal_id: The proposal ID to execute.
        """
        self._require_initialized()
        executor.require_auth()
        self._require_no_reentrant()

        proposal = self._get_proposal(proposal_id)
        status = proposal["status"]

        now = self.env.ledger().timestamp()

        if status == ProposalState.SUBMITTED:
            # Unchallenged. Must have passed challenge period.
            if now <= proposal["challenge_end"]:
                raise ContractError.CHALLENGE_PERIOD_ACTIVE
            
            # Return proposer bond
            proposer_bond = proposal["proposer_bond"]
            if proposer_bond > U128(0):
                bond_token = self.storage.get("bond_token")
                contract_addr = self.env.current_contract_address()
                self.env.invoke_contract(bond_token, "transfer", [contract_addr, proposal["proposer"], proposer_bond])
        elif status == ProposalState.APPROVED:
            # Challenged and approved by votes
            pass
        else:
            raise ContractError.INVALID_STATE

        # Lock execution
        self.storage.set("execution_lock", True)

        targets = proposal["targets"]
        values = proposal["values"]
        calldatas = proposal["calldatas"]

        for i in range(len(targets)):
            success = self.env.invoke_contract(targets[i], calldatas[i], values[i])
            if not success:
                self.storage.set("execution_lock", False)
                raise ContractError.EXECUTION_FAILED

        proposal["status"] = ProposalState.EXECUTED
        self.storage.set(("proposal", proposal_id), proposal)
        self.storage.set("execution_lock", False)

        self.env.emit_event("proposal_executed", {
            "proposal_id": proposal_id,
            "executor": executor,
        })

    @external
    def cancel(self, caller: Address, proposal_id: U64):
        """Cancel proposal before challenge/execution. Proposer gets bond back.

        Args:
            caller: Must be the proposal's creator.
            proposal_id: The proposal ID.
        """
        self._require_initialized()
        caller.require_auth()
        self._require_no_reentrant()

        proposal = self._get_proposal(proposal_id)
        if proposal["proposer"] != caller:
            raise ContractError.UNAUTHORIZED
        if proposal["status"] != ProposalState.SUBMITTED:
            raise ContractError.INVALID_STATE

        # Refund proposer bond
        proposer_bond = proposal["proposer_bond"]
        if proposer_bond > U128(0):
            bond_token = self.storage.get("bond_token")
            contract_addr = self.env.current_contract_address()
            self.env.invoke_contract(bond_token, "transfer", [contract_addr, caller, proposer_bond])

        proposal["status"] = ProposalState.CANCELED
        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("proposal_cancelled", {
            "proposal_id": proposal_id,
            "cancelled_by": caller,
        })

    # ------------------------------------------------------------------ #
    #  Admin Config Functions                                              #
    # ------------------------------------------------------------------ #

    @external
    def update_challenge_period(self, admin: Address, new_period: U64):
        """Update the challenge period duration. Only Admin."""
        self._require_admin(admin)
        if new_period < MIN_CHALLENGE_PERIOD or new_period > MAX_CHALLENGE_PERIOD:
            raise ContractError.INVALID_CHALLENGE_PERIOD
        self.storage.set("challenge_period", new_period)
        self.env.emit_event("challenge_period_updated", {"new_period": new_period})

    @external
    def update_voting_period(self, admin: Address, new_period: U64):
        """Update the voting period duration. Only Admin."""
        self._require_admin(admin)
        if new_period < MIN_VOTING_PERIOD or new_period > MAX_VOTING_PERIOD:
            raise ContractError.INVALID_VOTING_PERIOD
        self.storage.set("voting_period", new_period)
        self.env.emit_event("voting_period_updated", {"new_period": new_period})

    @external
    def update_bonds(self, admin: Address, new_proposer_bond: U128, new_challenger_bond: U128):
        """Update proposer and challenger bond amounts. Only Admin."""
        self._require_admin(admin)
        self.storage.set("proposer_bond", new_proposer_bond)
        self.storage.set("challenger_bond", new_challenger_bond)
        self.env.emit_event("bonds_updated", {
            "proposer_bond": new_proposer_bond,
            "challenger_bond": new_challenger_bond,
        })

    @external
    def update_quorum_bps(self, admin: Address, new_quorum_bps: U64):
        """Update voting quorum basis points. Only Admin."""
        self._require_admin(admin)
        if new_quorum_bps < MIN_QUORUM_BPS or new_quorum_bps > MAX_QUORUM_BPS:
            raise ContractError.INVALID_QUORUM
        self.storage.set("quorum_bps", new_quorum_bps)
        self.env.emit_event("quorum_updated", {"new_quorum_bps": new_quorum_bps})

    @external
    def transfer_admin(self, admin: Address, new_admin: Address):
        """Transfer admin role to new address. Only Admin."""
        self._require_admin(admin)
        self.storage.set("admin", new_admin)
        self.env.emit_event("admin_transferred", {
            "old_admin": admin,
            "new_admin": new_admin,
        })

    # ------------------------------------------------------------------ #
    #  View Functions                                                     #
    # ------------------------------------------------------------------ #

    @view
    def get_proposal(self, proposal_id: U64) -> Map:
        """Get the details of a proposal."""
        return self._get_proposal(proposal_id)

    @view
    def get_proposal_status(self, proposal_id: U64) -> U64:
        """Get the current status of a proposal."""
        proposal = self._get_proposal(proposal_id)
        return proposal["status"]

    @view
    def get_proposal_count(self) -> U64:
        """Get the total number of proposals created."""
        return self.storage.get("proposal_count", U64(0))

    @view
    def has_voted(self, proposal_id: U64, voter: Address) -> Bool:
        """Check if a voter has voted on a proposal."""
        return self.storage.get(("voted", proposal_id, voter), False)

    @view
    def get_config(self) -> Map:
        """Get the governance configuration parameters."""
        return {
            "admin": self.storage.get("admin"),
            "bond_token": self.storage.get("bond_token"),
            "governance_token": self.storage.get("governance_token"),
            "challenge_period": self.storage.get("challenge_period"),
            "voting_period": self.storage.get("voting_period"),
            "proposer_bond": self.storage.get("proposer_bond"),
            "challenger_bond": self.storage.get("challenger_bond"),
            "quorum_bps": self.storage.get("quorum_bps"),
        }

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                   #
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

    def _get_voting_power(self, voter: Address) -> U128:
        token = self.storage.get("governance_token")
        return self.env.invoke_contract(token, "balance", [voter])

    def _get_total_supply(self) -> U128:
        token = self.storage.get("governance_token")
        return self.env.invoke_contract(token, "total_supply", [])
