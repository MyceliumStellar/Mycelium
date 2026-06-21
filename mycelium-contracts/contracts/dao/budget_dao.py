"""
Budget DAO — Department management, quarterly budget voting, surplus clawbacks, and budget reallocation.

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
    DEPT_NOT_FOUND = 4
    PROPOSAL_NOT_FOUND = 5
    INVALID_STATE = 6
    INSUFFICIENT_BUDGET = 7
    ALREADY_VOTED = 8
    VOTING_ACTIVE = 9
    VOTING_ENDED = 10
    SURPLUS_ZERO = 11
    DEPT_INACTIVE = 12


class ProposalType:
    ALLOCATE = 0
    REALLOCATE = 1


class ProposalState:
    ACTIVE = 0
    DEFEATED = 1
    SUCCEEDED = 2
    EXECUTED = 3


@contract
class BudgetDAO:
    """A DAO contract coordinating department creation, quarterly budgets, reallocation, and surplus clawbacks."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        deposit_token: Address,
        voting_duration: U64,
        quorum_bps: U64,
    ):
        """Initialize the Budget DAO contract.

        Args:
            admin: Admin address who sets up departments and initiates clawbacks.
            deposit_token: Token used for budgets.
            voting_duration: Duration of voting periods for budget proposals.
            quorum_bps: Quorum in basis points (100 = 1%).
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("token", deposit_token)
        self.storage.set("voting_duration", voting_duration)
        self.storage.set("quorum_bps", quorum_bps)

        self.storage.set("dept_count", U64(0))
        self.storage.set("proposal_count", U64(0))
        self.storage.set("current_quarter", U64(1))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "token": deposit_token,
            "voting_duration": voting_duration,
        })

    @external
    def create_department(self, admin: Address, manager: Address, name: Symbol) -> U64:
        """Register a new department within the DAO. Only admin.

        Args:
            admin: Admin address.
            manager: Address of the department manager.
            name: Department name symbol.
        """
        self._require_initialized()
        self._require_admin(admin)

        dept_id = self.storage.get("dept_count") + U64(1)
        self.storage.set("dept_count", dept_id)

        dept = {
            "id": dept_id,
            "manager": manager,
            "name": name,
            "active": True,
        }

        self.storage.set(("department", dept_id), dept)

        self.env.emit_event("department_created", {
            "dept_id": dept_id,
            "manager": manager,
            "name": name,
        })

        return dept_id

    @external
    def propose_budget(
        self,
        proposer: Address,
        dept_id: U64,
        quarter: U64,
        amount: U128,
        description: Symbol,
    ) -> U64:
        """Propose budget allocation for a department in a specific quarter.

        Args:
            proposer: Proposer address.
            dept_id: Target department ID.
            quarter: The target quarter.
            amount: Budget amount.
            description: Description symbol.
        """
        self._require_initialized()
        proposer.require_auth()

        dept = self._get_department(dept_id)
        if not dept["active"]:
            raise ContractError.DEPT_INACTIVE

        proposal_id = self.storage.get("proposal_count") + U64(1)
        self.storage.set("proposal_count", proposal_id)

        now = self.env.ledger().timestamp()
        vote_end = now + self.storage.get("voting_duration")

        proposal = {
            "id": proposal_id,
            "type": ProposalType.ALLOCATE,
            "dept_id": dept_id,
            "target_dept_id": U64(0), # Unused for allocate
            "quarter": quarter,
            "amount": amount,
            "description": description,
            "vote_end": vote_end,
            "votes_for": U128(0),
            "votes_against": U128(0),
            "executed": False,
        }

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("budget_proposed", {
            "proposal_id": proposal_id,
            "dept_id": dept_id,
            "quarter": quarter,
            "amount": amount,
        })

        return proposal_id

    @external
    def propose_reallocation(
        self,
        proposer: Address,
        from_dept_id: U64,
        to_dept_id: U64,
        quarter: U64,
        amount: U128,
        description: Symbol,
    ) -> U64:
        """Propose moving allocated budget from one department to another.

        Args:
            proposer: Proposer address.
            from_dept_id: Department ID to withdraw budget from.
            to_dept_id: Department ID to allocate budget to.
            quarter: The target quarter.
            amount: Reallocation amount.
            description: Description symbol.
        """
        self._require_initialized()
        proposer.require_auth()

        from_dept = self._get_department(from_dept_id)
        to_dept = self._get_department(to_dept_id)
        if not from_dept["active"] or not to_dept["active"]:
            raise ContractError.DEPT_INACTIVE

        # Check that target department has enough remaining budget in the specified quarter
        budget_from = self._get_dept_budget(from_dept_id, quarter)
        remaining = budget_from["allocated"] - budget_from["spent"]
        if remaining < amount:
            raise ContractError.INSUFFICIENT_BUDGET

        proposal_id = self.storage.get("proposal_count") + U64(1)
        self.storage.set("proposal_count", proposal_id)

        now = self.env.ledger().timestamp()
        vote_end = now + self.storage.get("voting_duration")

        proposal = {
            "id": proposal_id,
            "type": ProposalType.REALLOCATE,
            "dept_id": from_dept_id,
            "target_dept_id": to_dept_id,
            "quarter": quarter,
            "amount": amount,
            "description": description,
            "vote_end": vote_end,
            "votes_for": U128(0),
            "votes_against": U128(0),
            "executed": False,
        }

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("reallocation_proposed", {
            "proposal_id": proposal_id,
            "from_dept_id": from_dept_id,
            "to_dept_id": to_dept_id,
            "amount": amount,
        })

        return proposal_id

    @external
    def cast_vote(self, voter: Address, proposal_id: U64, vote_type: U64):
        """Vote on budget proposals using voter's token balance weight.

        Args:
            voter: DAO member address.
            proposal_id: Proposal ID.
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
            raise ContractError.UNAUTHORIZED

        if vote_type == U64(1):
            proposal["votes_for"] = proposal["votes_for"] + voting_power
        elif vote_type == U64(0):
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
    def execute_proposal(self, executor: Address, proposal_id: U64):
        """Execute a succeeded budget or reallocation proposal.

        Args:
            executor: Trigger address.
            proposal_id: Proposal ID.
        """
        self._require_initialized()
        executor.require_auth()

        proposal = self._get_proposal(proposal_id)
        state = self._compute_state(proposal)
        if state != ProposalState.SUCCEEDED:
            raise ContractError.INVALID_STATE

        token = self.storage.get("token")
        cash_balance = self.env.invoke_contract(token, "balance", [self.env.current_contract_address()])

        if proposal["type"] == ProposalType.ALLOCATE:
            # Check if treasury can cover the allocation
            # We track budgets off-ledger on contract storage, so we ensure cash is sufficient
            total_allocated_all = self._get_total_allocated()
            if cash_balance < total_allocated_all + proposal["amount"]:
                raise ContractError.INSUFFICIENT_BUDGET

            budget = self._get_dept_budget(proposal["dept_id"], proposal["quarter"])
            budget["allocated"] = budget["allocated"] + proposal["amount"]
            self.storage.set(("budget", proposal["dept_id"], proposal["quarter"]), budget)

        elif proposal["type"] == ProposalType.REALLOCATE:
            budget_from = self._get_dept_budget(proposal["dept_id"], proposal["quarter"])
            if budget_from["allocated"] - budget_from["spent"] < proposal["amount"]:
                raise ContractError.INSUFFICIENT_BUDGET

            budget_to = self._get_dept_budget(proposal["target_dept_id"], proposal["quarter"])

            budget_from["allocated"] = budget_from["allocated"] - proposal["amount"]
            budget_to["allocated"] = budget_to["allocated"] + proposal["amount"]

            self.storage.set(("budget", proposal["dept_id"], proposal["quarter"]), budget_from)
            self.storage.set(("budget", proposal["target_dept_id"], proposal["quarter"]), budget_to)

        proposal["executed"] = True
        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("proposal_executed", {
            "proposal_id": proposal_id,
            "type": proposal["type"],
        })

    @external
    def department_spend(
        self,
        manager: Address,
        dept_id: U64,
        quarter: U64,
        recipient: Address,
        amount: U128,
        description: Symbol,
    ):
        """Execute a spending transfer within a department's quarterly budget. Only manager.

        Args:
            manager: Department manager address.
            dept_id: Department ID.
            quarter: The quarter index.
            recipient: Spend recipient.
            amount: Token amount.
            description: Description symbol.
        """
        self._require_initialized()
        manager.require_auth()

        dept = self._get_department(dept_id)
        if dept["manager"] != manager:
            raise ContractError.UNAUTHORIZED
        if not dept["active"]:
            raise ContractError.DEPT_INACTIVE

        budget = self._get_dept_budget(dept_id, quarter)
        remaining = budget["allocated"] - budget["spent"]
        if remaining < amount:
            raise ContractError.INSUFFICIENT_BUDGET

        budget["spent"] = budget["spent"] + amount
        self.storage.set(("budget", dept_id, quarter), budget)

        # Disburse tokens
        token = self.storage.get("token")
        success = self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), recipient, amount])
        if not success:
            raise ContractError.INSUFFICIENT_BUDGET

        self.env.emit_event("department_spend", {
            "dept_id": dept_id,
            "quarter": quarter,
            "recipient": recipient,
            "amount": amount,
        })

    @external
    def clawback_surplus(self, admin: Address, dept_id: U64, quarter: U64):
        """Claw back unspent surplus of a department budget at quarter end. Only admin.

        Args:
            admin: Admin address.
            dept_id: Department ID.
            quarter: Quarter index.
        """
        self._require_initialized()
        self._require_admin(admin)

        current_q = self.storage.get("current_quarter")
        if quarter >= current_q:
            raise ContractError.INVALID_STATE  # Cannot claw back current or future quarters

        budget = self._get_dept_budget(dept_id, quarter)
        surplus = budget["allocated"] - budget["spent"]
        if surplus == U128(0):
            raise ContractError.SURPLUS_ZERO

        # Reduce allocated amount to spent (effectively reclaiming surplus)
        budget["allocated"] = budget["spent"]
        self.storage.set(("budget", dept_id, quarter), budget)

        self.env.emit_event("surplus_clawed_back", {
            "dept_id": dept_id,
            "quarter": quarter,
            "clawback_amount": surplus,
        })

    @external
    def increment_quarter(self, admin: Address):
        """Advance the DAO to the next calendar/budget quarter. Only admin.

        Args:
            admin: Admin address.
        """
        self._require_initialized()
        self._require_admin(admin)

        current_q = self.storage.get("current_quarter")
        next_q = current_q + U64(1)
        self.storage.set("current_quarter", next_q)

        self.env.emit_event("quarter_advanced", {
            "new_quarter": next_q,
        })

    @view
    def get_department(self, dept_id: U64) -> Map:
        """Get department registration info."""
        return self._get_department(dept_id)

    @view
    def get_budget(self, dept_id: U64, quarter: U64) -> Map:
        """Get quarterly department budget status."""
        return self._get_dept_budget(dept_id, quarter)

    @view
    def get_proposal(self, proposal_id: U64) -> Map:
        """Get proposal info and compute state."""
        proposal = self._get_proposal(proposal_id)
        proposal["state"] = self._compute_state(proposal)
        return proposal

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        caller.require_auth()
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _get_department(self, dept_id: U64) -> Map:
        dept = self.storage.get(("department", dept_id), None)
        if dept is None:
            raise ContractError.DEPT_NOT_FOUND
        return dept

    def _get_proposal(self, proposal_id: U64) -> Map:
        proposal = self.storage.get(("proposal", proposal_id), None)
        if proposal is None:
            raise ContractError.PROPOSAL_NOT_FOUND
        return proposal

    def _get_dept_budget(self, dept_id: U64, quarter: U64) -> Map:
        budget = self.storage.get(("budget", dept_id, quarter), None)
        if budget is None:
            return {
                "allocated": U128(0),
                "spent": U128(0),
            }
        return budget

    def _get_total_allocated(self) -> U128:
        dept_count = self.storage.get("dept_count")
        current_q = self.storage.get("current_quarter")
        total = U128(0)
        for d in range(1, int(dept_count) + 1):
            budget = self._get_dept_budget(U64(d), current_q)
            total = total + (budget["allocated"] - budget["spent"])
        return total

    def _compute_state(self, proposal: Map) -> U64:
        if proposal["executed"]:
            return ProposalState.EXECUTED

        now = self.env.ledger().timestamp()
        if now < proposal["vote_end"]:
            return ProposalState.ACTIVE

        total_supply = self._get_total_supply()
        quorum_bps = self.storage.get("quorum_bps")
        required_quorum = (total_supply * U128(quorum_bps)) / U128(10000)

        total_votes = proposal["votes_for"] + proposal["votes_against"]
        if total_votes < required_quorum:
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
