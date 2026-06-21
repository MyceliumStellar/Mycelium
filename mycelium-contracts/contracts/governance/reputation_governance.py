"""
Reputation Governance — Governance system using non-transferable reputation with inactivity decay.

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
    INVALID_DECAY_PARAMS = 4
    INVALID_VOTING_PERIOD = 5
    BELOW_PROPOSAL_THRESHOLD = 6
    PROPOSAL_NOT_FOUND = 7
    INVALID_STATE = 8
    ALREADY_VOTED = 9
    VOTING_ENDED = 10
    VOTING_NOT_ENDED = 11
    ZERO_VOTING_POWER = 12
    EXECUTION_FAILED = 13
    SELF_REFERENTIAL_EXECUTION = 14
    REENTRANT_CALL = 15
    INSUFFICIENT_REPUTATION = 16


class ProposalState:
    PENDING = 0
    ACTIVE = 1
    DEFEATED = 2
    SUCCEEDED = 3
    EXECUTED = 4
    CANCELED = 5


# Limits
MIN_DECAY_GRACE = 86400            # 1 day
MIN_DECAY_INTERVAL = 3600          # 1 hour
MAX_DECAY_RATE_BPS = 5000          # 50% max decay per interval
MIN_VOTING_PERIOD = 3600           # 1 hour
MAX_ACTIONS = 10


@contract
class ReputationGovernance:
    """Reputation-based governance where voting power is derived from non-transferable
    reputation points that decay over periods of user inactivity."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        decay_grace_period: U64,
        decay_interval: U64,
        decay_rate_bps: U64,
        proposal_threshold: U128,
        voting_period: U64,
        quorum_bps: U64,
    ):
        """Initialize the Reputation Governance contract configuration."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        if decay_grace_period < MIN_DECAY_GRACE:
            raise ContractError.INVALID_DECAY_PARAMS
        if decay_interval < MIN_DECAY_INTERVAL:
            raise ContractError.INVALID_DECAY_PARAMS
        if decay_rate_bps > MAX_DECAY_RATE_BPS:
            raise ContractError.INVALID_DECAY_PARAMS
        if voting_period < MIN_VOTING_PERIOD:
            raise ContractError.INVALID_VOTING_PERIOD

        self.storage.set("admin", admin)
        self.storage.set("decay_grace_period", decay_grace_period)
        self.storage.set("decay_interval", decay_interval)
        self.storage.set("decay_rate_bps", decay_rate_bps)
        self.storage.set("proposal_threshold", proposal_threshold)
        self.storage.set("voting_period", voting_period)
        self.storage.set("quorum_bps", quorum_bps)
        
        self.storage.set("total_reputation", U128(0))
        self.storage.set("proposal_count", U64(0))
        self.storage.set("execution_lock", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "decay_grace_period": decay_grace_period,
            "decay_interval": decay_interval,
            "decay_rate_bps": decay_rate_bps,
            "proposal_threshold": proposal_threshold,
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
        """Submit a new governance proposal using reputation power.

        Args:
            proposer: The address of the proposal creator.
            targets: Target contract addresses.
            values: Native token amounts.
            calldatas: Binary calldatas.
            description: Description symbol.
        """
        self._require_initialized()
        proposer.require_auth()
        self._require_no_reentrant()

        if len(targets) == 0 or len(targets) > MAX_ACTIONS:
            raise ContractError.INVALID_STATE
        if len(targets) != len(values) or len(targets) != len(calldatas):
            raise ContractError.INVALID_STATE

        # Update proposer's activity to apply decay before checking threshold
        self._update_activity(proposer)

        rep_power = self._get_reputation(proposer)
        threshold = self.storage.get("proposal_threshold")
        if rep_power < threshold:
            raise ContractError.BELOW_PROPOSAL_THRESHOLD

        contract_addr = self.env.current_contract_address()
        for target in targets:
            if target == contract_addr:
                raise ContractError.SELF_REFERENTIAL_EXECUTION

        proposal_id = self.storage.get("proposal_count") + U64(1)
        self.storage.set("proposal_count", proposal_id)

        now = self.env.ledger().timestamp()
        voting_period = self.storage.get("voting_period")
        vote_start = now
        vote_end = now + voting_period

        total_rep = self.storage.get("total_reputation")

        proposal = {
            "id": proposal_id,
            "proposer": proposer,
            "targets": targets,
            "values": values,
            "calldatas": calldatas,
            "description": description,
            "vote_start": vote_start,
            "vote_end": vote_end,
            "total_rep_snapshot": total_rep,
            "votes_for": U128(0),
            "votes_against": U128(0),
            "executed": False,
            "canceled": False,
        }

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("proposal_created", {
            "proposal_id": proposal_id,
            "proposer": proposer,
            "vote_end": vote_end,
            "total_rep_snapshot": total_rep,
        })

        return proposal_id

    @external
    def cast_vote(self, voter: Address, proposal_id: U64, vote_for: Bool):
        """Cast a vote on an active proposal using decayed reputation.

        Args:
            voter: The address of the voter.
            proposal_id: The ID of the proposal.
            vote_for: True to support, False to oppose.
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

        # Update voter's activity, resetting their decay timer
        self._update_activity(voter)

        rep_power = self._get_reputation(voter)
        if rep_power == U128(0):
            raise ContractError.ZERO_VOTING_POWER

        if vote_for:
            proposal["votes_for"] = proposal["votes_for"] + rep_power
        else:
            proposal["votes_against"] = proposal["votes_against"] + rep_power

        self.storage.set(("voted", proposal_id, voter), True)
        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("vote_cast", {
            "proposal_id": proposal_id,
            "voter": voter,
            "vote_for": vote_for,
            "reputation_power": rep_power,
        })

    @external
    def execute(self, executor: Address, proposal_id: U64):
        """Execute a succeeded proposal.

        Args:
            executor: Address executing the transaction.
            proposal_id: Proposal ID.
        """
        self._require_initialized()
        executor.require_auth()
        self._require_no_reentrant()

        proposal = self._get_proposal(proposal_id)
        state = self._compute_state(proposal)
        if state != ProposalState.SUCCEEDED:
            raise ContractError.INVALID_STATE

        self.storage.set("execution_lock", True)

        targets = proposal["targets"]
        values = proposal["values"]
        calldatas = proposal["calldatas"]

        for i in range(len(targets)):
            success = self.env.invoke_contract(targets[i], calldatas[i], values[i])
            if not success:
                self.storage.set("execution_lock", False)
                raise ContractError.EXECUTION_FAILED

        proposal["executed"] = True
        self.storage.set(("proposal", proposal_id), proposal)
        self.storage.set("execution_lock", False)

        # Reward the proposer with extra reputation for a successful execution
        self._mint_reputation(proposal["proposer"], U128(100))  # standard execution reward

        self.env.emit_event("proposal_executed", {
            "proposal_id": proposal_id,
            "executor": executor,
        })

    @external
    def cancel(self, caller: Address, proposal_id: U64):
        """Cancel a proposal. Only proposer or admin.

        Args:
            caller: Must be the proposer or admin.
            proposal_id: Proposal ID.
        """
        self._require_initialized()
        caller.require_auth()

        proposal = self._get_proposal(proposal_id)
        state = self._compute_state(proposal)
        if state == ProposalState.EXECUTED or state == ProposalState.CANCELED:
            raise ContractError.INVALID_STATE

        admin = self.storage.get("admin")
        if caller != proposal["proposer"] and caller != admin:
            raise ContractError.UNAUTHORIZED

        proposal["canceled"] = True
        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("proposal_canceled", {
            "proposal_id": proposal_id,
            "cancelled_by": caller,
        })

    # ------------------------------------------------------------------ #
    #  Reputation Management (Admin Only)                                  #
    # ------------------------------------------------------------------ #

    @external
    def reward_reputation(self, admin: Address, user: Address, amount: U128):
        """Mint/add reputation to a user. Only Admin."""
        self._require_admin(admin)
        self._update_activity(user)
        self._mint_reputation(user, amount)

    @external
    def burn_reputation(self, admin: Address, user: Address, amount: U128):
        """Burn/remove reputation from a user. Only Admin."""
        self._require_admin(admin)
        self._update_activity(user)
        self._burn_reputation(user, amount)

    @external
    def update_decay_params(
        self,
        admin: Address,
        decay_grace_period: U64,
        decay_interval: U64,
        decay_rate_bps: U64,
    ):
        """Update reputation decay parameters. Only Admin."""
        self._require_admin(admin)
        if decay_grace_period < MIN_DECAY_GRACE:
            raise ContractError.INVALID_DECAY_PARAMS
        if decay_interval < MIN_DECAY_INTERVAL:
            raise ContractError.INVALID_DECAY_PARAMS
        if decay_rate_bps > MAX_DECAY_RATE_BPS:
            raise ContractError.INVALID_DECAY_PARAMS

        self.storage.set("decay_grace_period", decay_grace_period)
        self.storage.set("decay_interval", decay_interval)
        self.storage.set("decay_rate_bps", decay_rate_bps)

        self.env.emit_event("decay_params_updated", {
            "decay_grace_period": decay_grace_period,
            "decay_interval": decay_interval,
            "decay_rate_bps": decay_rate_bps,
        })

    @external
    def update_proposal_threshold(self, admin: Address, new_threshold: U128):
        """Update minimum reputation threshold for proposal submission. Only Admin."""
        self._require_admin(admin)
        self.storage.set("proposal_threshold", new_threshold)
        self.env.emit_event("proposal_threshold_updated", {"new_threshold": new_threshold})

    @external
    def update_voting_period(self, admin: Address, new_period: U64):
        """Update voting period duration. Only Admin."""
        self._require_admin(admin)
        if new_period < MIN_VOTING_PERIOD:
            raise ContractError.INVALID_VOTING_PERIOD
        self.storage.set("voting_period", new_period)
        self.env.emit_event("voting_period_updated", {"new_period": new_period})

    @external
    def update_quorum_bps(self, admin: Address, new_quorum_bps: U64):
        """Update quorum threshold in basis points. Only Admin."""
        self._require_admin(admin)
        self.storage.set("quorum_bps", new_quorum_bps)
        self.env.emit_event("quorum_updated", {"new_quorum_bps": new_quorum_bps})

    @external
    def transfer_admin(self, admin: Address, new_admin: Address):
        """Transfer admin role. Only Admin."""
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
    def get_reputation(self, user: Address) -> U128:
        """Return the current reputation of a user after applying decay."""
        self._require_initialized()
        return self._get_decayed_reputation(user)

    @view
    def get_last_active(self, user: Address) -> U64:
        """Return the last active timestamp of a user."""
        self._require_initialized()
        return self.storage.get(("last_active", user), U64(0))

    @view
    def get_total_reputation(self) -> U128:
        """Return the total reputation minted across all users (not decayed)."""
        self._require_initialized()
        return self.storage.get("total_reputation", U128(0))

    @view
    def get_proposal(self, proposal_id: U64) -> Map:
        """Get proposal details with current computed state."""
        proposal = self._get_proposal(proposal_id)
        proposal["state"] = self._compute_state(proposal)
        return proposal

    @view
    def get_config(self) -> Map:
        """Get governance and reputation parameters."""
        return {
            "admin": self.storage.get("admin"),
            "decay_grace_period": self.storage.get("decay_grace_period"),
            "decay_interval": self.storage.get("decay_interval"),
            "decay_rate_bps": self.storage.get("decay_rate_bps"),
            "proposal_threshold": self.storage.get("proposal_threshold"),
            "voting_period": self.storage.get("voting_period"),
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

    def _get_reputation(self, user: Address) -> U128:
        return self.storage.get(("reputation", user), U128(0))

    def _get_decayed_reputation(self, user: Address) -> U128:
        """Calculate the reputation of a user after applying decay over inactivity."""
        rep = self._get_reputation(user)
        if rep == U128(0):
            return U128(0)

        last_active = self.storage.get(("last_active", user), U64(0))
        if last_active == U64(0):
            return rep

        now = self.env.ledger().timestamp()
        if now <= last_active:
            return rep

        inactive_duration = now - last_active
        grace_period = self.storage.get("decay_grace_period")

        if inactive_duration <= grace_period:
            return rep

        decay_time = inactive_duration - grace_period
        interval = self.storage.get("decay_interval")
        intervals_passed = U128(decay_time / interval)

        if intervals_passed == U128(0):
            return rep

        decay_rate = self.storage.get("decay_rate_bps")
        
        # Apply decay: rep = rep * (1 - decay_rate/10000)^intervals
        # For simplicity and gas, we apply it step by step up to a reasonable limit (e.g. 20 intervals),
        # or we just subtract flat/percentage decay.
        decayed_rep = rep
        for _ in range(20):  # Cap loop to prevent CPU timeout
            if intervals_passed == U128(0) or decayed_rep == U128(0):
                break
            decay_amount = (decayed_rep * U128(decay_rate)) / U128(10000)
            if decay_amount == U128(0):
                decay_amount = U128(1)  # Minimum decay of 1 point if rate > 0
            if decayed_rep > decay_amount:
                decayed_rep = decayed_rep - decay_amount
            else:
                decayed_rep = U128(0)
            intervals_passed = intervals_passed - U128(1)

        return decayed_rep

    def _update_activity(self, user: Address):
        """Update user activity, committing decay and resetting inactivity timer."""
        decayed_rep = self._get_decayed_reputation(user)
        old_rep = self._get_reputation(user)
        
        if decayed_rep != old_rep:
            self.storage.set(("reputation", user), decayed_rep)
            total_rep = self.storage.get("total_reputation")
            diff = old_rep - decayed_rep
            if total_rep >= diff:
                self.storage.set("total_reputation", total_rep - diff)
            else:
                self.storage.set("total_reputation", U128(0))
            
            self.env.emit_event("reputation_decayed", {
                "user": user,
                "amount": diff,
                "new_reputation": decayed_rep,
            })

        self.storage.set(("last_active", user), self.env.ledger().timestamp())

    def _mint_reputation(self, user: Address, amount: U128):
        if amount == U128(0):
            return
        rep = self._get_reputation(user)
        new_rep = rep + amount
        self.storage.set(("reputation", user), new_rep)

        total_rep = self.storage.get("total_reputation")
        self.storage.set("total_reputation", total_rep + amount)

        self.env.emit_event("reputation_minted", {
            "user": user,
            "amount": amount,
            "new_reputation": new_rep,
        })

    def _burn_reputation(self, user: Address, amount: U128):
        if amount == U128(0):
            return
        rep = self._get_reputation(user)
        if rep < amount:
            raise ContractError.INSUFFICIENT_REPUTATION
        new_rep = rep - amount
        self.storage.set(("reputation", user), new_rep)

        total_rep = self.storage.get("total_reputation")
        if total_rep >= amount:
            self.storage.set("total_reputation", total_rep - amount)
        else:
            self.storage.set("total_reputation", U128(0))

        self.env.emit_event("reputation_burned", {
            "user": user,
            "amount": amount,
            "new_reputation": new_rep,
        })

    def _compute_state(self, proposal: Map) -> U64:
        if proposal["canceled"]:
            return ProposalState.CANCELED
        if proposal["executed"]:
            return ProposalState.EXECUTED

        now = self.env.ledger().timestamp()

        if now < proposal["vote_start"]:
            return ProposalState.PENDING

        if now <= proposal["vote_end"]:
            return ProposalState.ACTIVE

        votes_for = proposal["votes_for"]
        votes_against = proposal["votes_against"]
        total_votes = votes_for + votes_against

        # Quorum based on total active reputation snapshot
        quorum_bps = self.storage.get("quorum_bps")
        required_quorum = (proposal["total_rep_snapshot"] * U128(quorum_bps)) / U128(10000)

        if total_votes >= required_quorum and votes_for > votes_against:
            return ProposalState.SUCCEEDED
        else:
            return ProposalState.DEFEATED
