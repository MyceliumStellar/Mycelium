"""
Sub-DAO Factory — Multi-level child DAO creation, budget delegation, parent veto, cross-DAO proposals, and dissolution.

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
    SUB_DAO_NOT_FOUND = 4
    PROPOSAL_NOT_FOUND = 5
    INVALID_STATE = 6
    INSUFFICIENT_BUDGET = 7
    ALREADY_VOTED = 8
    CROSS_DAO_MISMATCH = 9
    PROPOSAL_VETOED = 10
    SUB_DAO_INACTIVE = 11


class ProposalType:
    SPEND = 0
    CROSS_DAO = 1


class ProposalState:
    ACTIVE = 0
    SUCCEEDED = 1
    DEFEATED = 2
    EXECUTED = 3
    VETOED = 4


@contract
class SubDAOFactory:
    """Factory and management contract for deploying and governing child/sub DAOs."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, parent_admin: Address, deposit_token: Address):
        """Initialize the Sub-DAO Factory.

        Args:
            parent_admin: The main/parent DAO admin address.
            deposit_token: Asset token used for budgets.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        parent_admin.require_auth()

        self.storage.set("parent_admin", parent_admin)
        self.storage.set("token", deposit_token)
        self.storage.set("sub_dao_count", U64(0))
        self.storage.set("proposal_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "parent_admin": parent_admin,
            "token": deposit_token,
        })

    @external
    def create_sub_dao(
        self,
        parent: Address,
        sub_admin: Address,
        name: Symbol,
        initial_budget: U128,
    ) -> U64:
        """Deploy a new child DAO and allocate a budget to it. Only parent admin.

        Args:
            parent: Parent DAO admin address.
            sub_admin: Admin of the new Sub-DAO.
            name: Symbol name of the Sub-DAO.
            initial_budget: Budget allocated from parent treasury.
        """
        self._require_initialized()
        self._require_parent_admin(parent)

        # Check that factory treasury has enough tokens to back the budget
        token = self.storage.get("token")
        cash_balance = self.env.invoke_contract(token, "balance", [self.env.current_contract_address()])

        # Calculate currently allocated budget across all sub DAOs
        sub_dao_count = self.storage.get("sub_dao_count")
        total_allocated = U128(0)
        for i in range(1, int(sub_dao_count) + 1):
            sd = self.storage.get(("sub_dao", U64(i)))
            if sd["active"]:
                total_allocated = total_allocated + (sd["budget"] - sd["spent"])

        if cash_balance < total_allocated + initial_budget:
            raise ContractError.INSUFFICIENT_BUDGET

        sub_dao_id = sub_dao_count + U64(1)
        self.storage.set("sub_dao_count", sub_dao_id)

        sub_dao = {
            "id": sub_dao_id,
            "admin": sub_admin,
            "name": name,
            "budget": initial_budget,
            "spent": U128(0),
            "active": True,
        }

        self.storage.set(("sub_dao", sub_dao_id), sub_dao)
        # Register sub_admin as member of the Sub-DAO
        self.storage.set(("membership", sub_dao_id, sub_admin), True)

        self.env.emit_event("sub_dao_created", {
            "sub_dao_id": sub_dao_id,
            "admin": sub_admin,
            "name": name,
            "budget": initial_budget,
        })

        return sub_dao_id

    @external
    def set_membership(self, sub_admin: Address, sub_dao_id: U64, member: Address, status: Bool):
        """Add or remove a member of a Sub-DAO. Only Sub-DAO admin.

        Args:
            sub_admin: Sub-DAO admin address.
            sub_dao_id: Child DAO ID.
            member: Address to modify.
            status: True to add, False to remove.
        """
        self._require_initialized()
        sub_admin.require_auth()

        sub_dao = self._get_sub_dao(sub_dao_id)
        if not sub_dao["active"]:
            raise ContractError.SUB_DAO_INACTIVE
        if sub_dao["admin"] != sub_admin:
            raise ContractError.UNAUTHORIZED

        self.storage.set(("membership", sub_dao_id, member), status)

        self.env.emit_event("membership_updated", {
            "sub_dao_id": sub_dao_id,
            "member": member,
            "status": status,
        })

    @external
    def propose_spend(
        self,
        proposer: Address,
        sub_dao_id: U64,
        recipient: Address,
        amount: U128,
        description: Symbol,
        duration: U64,
    ) -> U64:
        """Create proposal to spend from Sub-DAO budget. Only Sub-DAO member.

        Args:
            proposer: Sub-DAO member address.
            sub_dao_id: Sub-DAO ID.
            recipient: Spend recipient address.
            amount: Amount requested.
            description: Spend description.
            duration: Voting duration in seconds.
        """
        self._require_initialized()
        proposer.require_auth()

        sub_dao = self._get_sub_dao(sub_dao_id)
        if not sub_dao["active"]:
            raise ContractError.SUB_DAO_INACTIVE

        is_member = self.storage.get(("membership", sub_dao_id, proposer), False)
        if not is_member:
            raise ContractError.UNAUTHORIZED

        # Ensure requested amount does not exceed remaining budget
        if sub_dao["budget"] - sub_dao["spent"] < amount:
            raise ContractError.INSUFFICIENT_BUDGET

        proposal_id = self.storage.get("proposal_count") + U64(1)
        self.storage.set("proposal_count", proposal_id)

        now = self.env.ledger().timestamp()
        vote_end = now + duration

        proposal = {
            "id": proposal_id,
            "type": ProposalType.SPEND,
            "sub_dao_id": sub_dao_id,
            "target_sub_dao_id": U64(0), # unused for basic spend
            "proposer": proposer,
            "recipient": recipient,
            "amount": amount,
            "description": description,
            "vote_end": vote_end,
            "votes_for": U64(0),
            "votes_against": U64(0),
            "approved_by_target": False, # unused for basic spend
            "state": ProposalState.ACTIVE,
        }

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("spend_proposed", {
            "proposal_id": proposal_id,
            "sub_dao_id": sub_dao_id,
            "amount": amount,
            "vote_end": vote_end,
        })

        return proposal_id

    @external
    def propose_cross_dao(
        self,
        proposer: Address,
        from_sub_dao_id: U64,
        to_sub_dao_id: U64,
        recipient: Address,
        amount: U128,
        description: Symbol,
        duration: U64,
    ) -> U64:
        """Propose a joint cross-DAO spending proposal funded by the originating Sub-DAO.

        Requires approval from the target Sub-DAO admin as well.
        """
        self._require_initialized()
        proposer.require_auth()

        from_dao = self._get_sub_dao(from_sub_dao_id)
        to_dao = self._get_sub_dao(to_sub_dao_id)

        if not from_dao["active"] or not to_dao["active"]:
            raise ContractError.SUB_DAO_INACTIVE

        is_member = self.storage.get(("membership", from_sub_dao_id, proposer), False)
        if not is_member:
            raise ContractError.UNAUTHORIZED

        if from_dao["budget"] - from_dao["spent"] < amount:
            raise ContractError.INSUFFICIENT_BUDGET

        proposal_id = self.storage.get("proposal_count") + U64(1)
        self.storage.set("proposal_count", proposal_id)

        now = self.env.ledger().timestamp()
        vote_end = now + duration

        proposal = {
            "id": proposal_id,
            "type": ProposalType.CROSS_DAO,
            "sub_dao_id": from_sub_dao_id,
            "target_sub_dao_id": to_sub_dao_id,
            "proposer": proposer,
            "recipient": recipient,
            "amount": amount,
            "description": description,
            "vote_end": vote_end,
            "votes_for": U64(0),
            "votes_against": U64(0),
            "approved_by_target": False,
            "state": ProposalState.ACTIVE,
        }

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("cross_dao_proposed", {
            "proposal_id": proposal_id,
            "from_sub_dao_id": from_sub_dao_id,
            "to_sub_dao_id": to_sub_dao_id,
            "amount": amount,
        })

        return proposal_id

    @external
    def cast_vote(self, voter: Address, proposal_id: U64, vote_type: U64):
        """Cast vote on Sub-DAO proposal. Open to members of the originating Sub-DAO.

        Args:
            voter: DAO member address.
            proposal_id: Proposal ID.
            vote_type: 0 for AGAINST, 1 for FOR.
        """
        self._require_initialized()
        voter.require_auth()

        proposal = self._get_proposal(proposal_id)
        if proposal["state"] != ProposalState.ACTIVE:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        if now >= proposal["vote_end"]:
            raise ContractError.INVALID_STATE

        # Voter must be a member of the proposal's originating Sub-DAO
        sub_dao_id = proposal["sub_dao_id"]
        is_member = self.storage.get(("membership", sub_dao_id, voter), False)
        if not is_member:
            raise ContractError.UNAUTHORIZED

        already_voted = self.storage.get(("voted", proposal_id, voter), False)
        if already_voted:
            raise ContractError.ALREADY_VOTED

        if vote_type == U64(1):
            proposal["votes_for"] = proposal["votes_for"] + U64(1)
        elif vote_type == U64(0):
            proposal["votes_against"] = proposal["votes_against"] + U64(1)
        else:
            raise ContractError.INVALID_STATE

        self.storage.set(("proposal", proposal_id), proposal)
        self.storage.set(("voted", proposal_id, voter), True)

        self.env.emit_event("vote_cast", {
            "proposal_id": proposal_id,
            "voter": voter,
            "vote_type": vote_type,
        })

    @external
    def approve_cross_dao_target(self, target_admin: Address, proposal_id: U64):
        """Approve cross-DAO proposal by target Sub-DAO admin.

        Args:
            target_admin: Admin of target Sub-DAO.
            proposal_id: Proposal ID.
        """
        self._require_initialized()
        target_admin.require_auth()

        proposal = self._get_proposal(proposal_id)
        if proposal["type"] != ProposalType.CROSS_DAO:
            raise ContractError.CROSS_DAO_MISMATCH
        if proposal["state"] != ProposalState.ACTIVE:
            raise ContractError.INVALID_STATE

        target_dao = self._get_sub_dao(proposal["target_sub_dao_id"])
        if target_dao["admin"] != target_admin:
            raise ContractError.UNAUTHORIZED

        proposal["approved_by_target"] = True
        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("cross_dao_approved_by_target", {
            "proposal_id": proposal_id,
            "target_sub_dao_id": proposal["target_sub_dao_id"],
        })

    @external
    def veto_proposal(self, parent_admin: Address, proposal_id: U64):
        """Veto a Sub-DAO proposal. Only Parent DAO admin.

        Args:
            parent_admin: Parent DAO admin address.
            proposal_id: Proposal ID to veto.
        """
        self._require_initialized()
        self._require_parent_admin(parent_admin)

        proposal = self._get_proposal(proposal_id)
        if proposal["state"] == ProposalState.EXECUTED or proposal["state"] == ProposalState.VETOED:
            raise ContractError.INVALID_STATE

        proposal["state"] = ProposalState.VETOED
        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("proposal_vetoed", {
            "proposal_id": proposal_id,
            "vetoed_by": parent_admin,
        })

    @external
    def execute_proposal(self, executor: Address, proposal_id: U64):
        """Execute a passed Sub-DAO proposal.

        Args:
            executor: Trigger address.
            proposal_id: Proposal ID.
        """
        self._require_initialized()
        executor.require_auth()

        proposal = self._get_proposal(proposal_id)
        if proposal["state"] == ProposalState.VETOED:
            raise ContractError.PROPOSAL_VETOED
        if proposal["state"] != ProposalState.ACTIVE:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        if now < proposal["vote_end"]:
            raise ContractError.INVALID_STATE

        # Quorum / Win validation: simple majority of votes cast (minimum 1 vote)
        total_votes = proposal["votes_for"] + proposal["votes_against"]
        passed = (total_votes > U64(0)) and (proposal["votes_for"] > proposal["votes_against"])

        if not passed:
            proposal["state"] = ProposalState.DEFEATED
            self.storage.set(("proposal", proposal_id), proposal)
            raise ContractError.INVALID_STATE

        # If it is a cross-DAO proposal, it also requires target Sub-DAO admin approval
        if proposal["type"] == ProposalType.CROSS_DAO and not proposal["approved_by_target"]:
            raise ContractError.INVALID_STATE

        # Deduct from Sub-DAO budget
        sub_dao_id = proposal["sub_dao_id"]
        sub_dao = self._get_sub_dao(sub_dao_id)

        if sub_dao["budget"] - sub_dao["spent"] < proposal["amount"]:
            raise ContractError.INSUFFICIENT_BUDGET

        sub_dao["spent"] = sub_dao["spent"] + proposal["amount"]
        self.storage.set(("sub_dao", sub_dao_id), sub_dao)

        # Disburse tokens
        token = self.storage.get("token")
        transfer_success = self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), proposal["recipient"], proposal["amount"]])
        if not transfer_success:
            raise ContractError.INSUFFICIENT_BUDGET

        proposal["state"] = ProposalState.EXECUTED
        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("proposal_executed", {
            "proposal_id": proposal_id,
            "sub_dao_id": sub_dao_id,
            "recipient": proposal["recipient"],
            "amount": proposal["amount"],
        })

    @external
    def dissolve_sub_dao(self, parent_admin: Address, sub_dao_id: U64):
        """Dissolve a Sub-DAO, returning all unspent budget to the parent treasury. Only parent admin.

        Args:
            parent_admin: Parent DAO admin address.
            sub_dao_id: Child DAO ID to dissolve.
        """
        self._require_initialized()
        self._require_parent_admin(parent_admin)

        sub_dao = self._get_sub_dao(sub_dao_id)
        if not sub_dao["active"]:
            raise ContractError.INVALID_STATE

        sub_dao["active"] = False
        # Retrieve unspent budget
        unspent = sub_dao["budget"] - sub_dao["spent"]
        sub_dao["budget"] = sub_dao["spent"]  # budget reduced to spent

        self.storage.set(("sub_dao", sub_dao_id), sub_dao)

        self.env.emit_event("sub_dao_dissolved", {
            "sub_dao_id": sub_dao_id,
            "unspent_clawback": unspent,
        })

    @view
    def get_sub_dao(self, sub_dao_id: U64) -> Map:
        """Get Sub-DAO details."""
        return self._get_sub_dao(sub_dao_id)

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

    def _require_parent_admin(self, caller: Address):
        caller.require_auth()
        parent_admin = self.storage.get("parent_admin")
        if caller != parent_admin:
            raise ContractError.UNAUTHORIZED

    def _get_sub_dao(self, sub_dao_id: U64) -> Map:
        sub_dao = self.storage.get(("sub_dao", sub_dao_id), None)
        if sub_dao is None:
            raise ContractError.SUB_DAO_NOT_FOUND
        return sub_dao

    def _get_proposal(self, proposal_id: U64) -> Map:
        proposal = self.storage.get(("proposal", proposal_id), None)
        if proposal is None:
            raise ContractError.PROPOSAL_NOT_FOUND
        return proposal
