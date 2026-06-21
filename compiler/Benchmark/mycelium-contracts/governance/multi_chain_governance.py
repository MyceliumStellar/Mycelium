"""
Multi-Chain Governance — Bridge-relayed cross-chain votes, chain weight adjustments, and synchronized proposal executions.

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
    CHAIN_NOT_CONFIGURED = 5
    VOTING_PERIOD_ACTIVE = 6
    ALREADY_EXECUTED = 7
    BRIDGE_ONLY = 8
    INVALID_WEIGHT = 9
    PROPOSAL_DEFEATED = 10
    CHAINS_NOT_SYNCHRONIZED = 11


class ProposalState:
    ACTIVE = 0
    DEFEATED = 1
    SUCCEEDED = 2
    EXECUTED = 3


@contract
class MultiChainGovernance:
    """Governance contract coordinating proposal creation, cross-chain voting weight aggregation, and execution."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        bridge: Address,
        voting_duration: U64,
        source_chain_ids: Vec,
        source_chain_weights: Vec,
    ):
        """Initialize Multi-Chain Governance contract.

        Args:
            admin: Admin address.
            bridge: Authorized cross-chain bridge address.
            voting_duration: Duration of local voting in seconds.
            source_chain_ids: Vec of chain IDs that vote on proposals (excluding Stellar).
            source_chain_weights: Vec of bps weight adjustments for each chain (10000 = 1.00).
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        n_chains = len(source_chain_ids)
        if n_chains != len(source_chain_weights):
            raise ContractError.INVALID_WEIGHT

        self.storage.set("admin", admin)
        self.storage.set("bridge", bridge)
        self.storage.set("voting_duration", voting_duration)
        self.storage.set("total_chains_count", U64(n_chains))

        for i in range(n_chains):
            chain_id = source_chain_ids[i]
            weight = source_chain_weights[i]
            if weight == U64(0):
                raise ContractError.INVALID_WEIGHT
            self.storage.set(("chain_weight", chain_id), weight)
            self.storage.set(("chain_active", chain_id), True)
            self.storage.set(("chain_index", U64(i)), chain_id)

        self.storage.set("proposal_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "bridge": bridge,
            "chain_count": U64(n_chains),
        })

    @external
    def propose(self, proposer: Address, target: Address, calldata: Bytes, description: Symbol) -> U64:
        """Create a proposal locally and dispatch a cross-chain sync message to the bridge.

        Args:
            proposer: Proposer address.
            target: Execution target contract on Stellar.
            calldata: Execution calldata.
            description: Proposal description.
        """
        self._require_initialized()
        proposer.require_auth()

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
            "yes_votes": U128(0),
            "no_votes": U128(0),
            "executed": False,
        }

        self.storage.set(("proposal", proposal_id), proposal)
        self.storage.set(("synced_chains", proposal_id), U64(0))

        # Dispatch cross-chain notification to all registered source chains via the bridge
        bridge = self.storage.get("bridge")
        chain_count = self.storage.get("total_chains_count")

        for i in range(chain_count):
            chain_id = self.storage.get(("chain_index", U64(i)))
            # Call bridge's message dispatch function
            # Bridge will relay the proposal details to chain_id to kick off voting there
            self.env.invoke_contract(bridge, "dispatch_proposal", [chain_id, proposal_id, description, vote_end])

        self.env.emit_event("proposal_created", {
            "proposal_id": proposal_id,
            "proposer": proposer,
            "vote_end": vote_end,
        })

        return proposal_id

    @external
    def cast_local_vote(self, voter: Address, proposal_id: U64, support: Bool, weight: U128):
        """Cast vote locally on the Stellar side.

        Args:
            voter: Voter address.
            proposal_id: Proposal ID.
            support: True for yes, False for no.
            weight: Local voting token weight.
        """
        self._require_initialized()
        voter.require_auth()

        proposal = self._get_proposal(proposal_id)
        if self.env.ledger().timestamp() >= proposal["vote_end"]:
            raise ContractError.VOTING_PERIOD_ACTIVE

        already_voted = self.storage.get(("voted_local", proposal_id, voter), False)
        if already_voted:
            raise ContractError.ALREADY_EXECUTED

        self.storage.set(("voted_local", proposal_id, voter), True)

        if support:
            proposal["yes_votes"] = proposal["yes_votes"] + weight
        else:
            proposal["no_votes"] = proposal["no_votes"] + weight

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("local_vote_cast", {
            "proposal_id": proposal_id,
            "voter": voter,
            "support": support,
            "weight": weight,
        })

    @external
    def receive_cross_chain_votes(
        self,
        bridge_caller: Address,
        proposal_id: U64,
        source_chain_id: U64,
        yes_votes: U128,
        no_votes: U128,
    ):
        """Callback triggered by the Bridge contract when voting results from another chain are received.

        Applies weight adjustment factors before accumulating.

        Args:
            bridge_caller: Must be the registered bridge contract.
            proposal_id: Proposal ID.
            source_chain_id: Source chain identifier.
            yes_votes: Raw yes votes count on source chain.
            no_votes: Raw no votes count on source chain.
        """
        self._require_initialized()
        bridge_caller.require_auth()

        # Auth check: must be from the trusted bridge
        trusted_bridge = self.storage.get("bridge")
        if bridge_caller != trusted_bridge:
            raise ContractError.BRIDGE_ONLY

        if not self.storage.get(("chain_active", source_chain_id), False):
            raise ContractError.CHAIN_NOT_CONFIGURED

        proposal = self._get_proposal(proposal_id)
        if proposal["executed"]:
            raise ContractError.ALREADY_EXECUTED

        # Prevent double-submitting for same chain
        already_synced = self.storage.get(("chain_synced", proposal_id, source_chain_id), False)
        if already_synced:
            raise ContractError.ALREADY_EXECUTED

        # Calculate weight adjustments: adjusted = raw * weight / 10000
        weight_bps = self.storage.get(("chain_weight", source_chain_id))
        adj_yes = (yes_votes * U128(weight_bps)) / U128(10000)
        adj_no = (no_votes * U128(weight_bps)) / U128(10000)

        # Accumulate to proposal total votes
        proposal["yes_votes"] = proposal["yes_votes"] + adj_yes
        proposal["no_votes"] = proposal["no_votes"] + adj_no

        self.storage.set(("chain_synced", proposal_id, source_chain_id), True)

        # Update synced chains count
        synced_count = self.storage.get(("synced_chains", proposal_id), U64(0)) + U64(1)
        self.storage.set(("synced_chains", proposal_id), synced_count)

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("cross_chain_votes_received", {
            "proposal_id": proposal_id,
            "chain_id": source_chain_id,
            "adjusted_yes": adj_yes,
            "adjusted_no": adj_no,
        })

    @external
    def execute_proposal(self, executor: Address, proposal_id: U64):
        """Execute proposal if all chains are synced and voting ended.

        Args:
            executor: Trigger address.
            proposal_id: Proposal ID.
        """
        self._require_initialized()
        executor.require_auth()

        proposal = self._get_proposal(proposal_id)
        if proposal["executed"]:
            raise ContractError.ALREADY_EXECUTED

        now = self.env.ledger().timestamp()
        if now < proposal["vote_end"]:
            raise ContractError.VOTING_PERIOD_ACTIVE

        # Ensure all cross-chain results are synced
        synced_count = self.storage.get(("synced_chains", proposal_id), U64(0))
        total_required = self.storage.get("total_chains_count")
        if synced_count < total_required:
            raise ContractError.CHAINS_NOT_SYNCHRONIZED

        passed = proposal["yes_votes"] > proposal["no_votes"]
        if not passed:
            raise ContractError.PROPOSAL_DEFEATED

        proposal["executed"] = True
        self.storage.set(("proposal", proposal_id), proposal)

        # Execute payload action
        success = self.env.invoke_contract(proposal["target"], "execute", [proposal["calldata"]])
        if not success:
            proposal["executed"] = False
            self.storage.set(("proposal", proposal_id), proposal)
            raise ContractError.PROPOSAL_DEFEATED

        self.env.emit_event("proposal_executed", {
            "proposal_id": proposal_id,
            "executor": executor,
            "yes_votes": proposal["yes_votes"],
            "no_votes": proposal["no_votes"],
        })

    @external
    def update_chain_weight(self, admin: Address, chain_id: U64, new_weight: U64):
        """Modify weight adjustment factor of a source chain. Only admin."""
        self._require_initialized()
        self._require_admin(admin)

        if not self.storage.get(("chain_active", chain_id), False):
            raise ContractError.CHAIN_NOT_CONFIGURED
        if new_weight == U64(0):
            raise ContractError.INVALID_WEIGHT

        self.storage.set(("chain_weight", chain_id), new_weight)

        self.env.emit_event("chain_weight_updated", {
            "chain_id": chain_id,
            "new_weight": new_weight,
        })

    @view
    def get_proposal(self, proposal_id: U64) -> Map:
        """Get proposal stats."""
        proposal = self._get_proposal(proposal_id)
        # Compute dynamic state
        synced = self.storage.get(("synced_chains", proposal_id), U64(0))
        total = self.storage.get("total_chains_count")
        proposal["all_chains_synced"] = synced == total
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

    def _get_proposal(self, proposal_id: U64) -> Map:
        proposal = self.storage.get(("proposal", proposal_id), None)
        if proposal is None:
            raise ContractError.PROPOSAL_NOT_FOUND
        return proposal
