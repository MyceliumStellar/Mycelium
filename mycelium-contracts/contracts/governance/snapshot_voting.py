"""
Snapshot Voting — Off-chain signature voting, Merkle Proof verification for on-chain execution, and gasless relays.

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
    INVALID_SIGNATURE = 5
    INVALID_PROOF = 6
    ALREADY_EXECUTED = 7
    PROPOSAL_DEFEATED = 8
    VOTING_PERIOD_ACTIVE = 9
    VOTING_PERIOD_ENDED = 10
    ALREADY_VOTED = 11


@contract
class SnapshotVoting:
    """Snapshot-style governance verifying off-chain results via Merkle roots and allowing signature-based voting."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, relayer: Address):
        """Initialize the Snapshot Voting contract.

        Args:
            admin: Admin address.
            relayer: Authorized off-chain snapshot sync relayer/oracle.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("relayer", relayer)
        self.storage.set("proposal_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "relayer": relayer,
        })

    @external
    def propose(
        self,
        proposer: Address,
        ipfs_hash: Symbol,
        duration: U64,
    ) -> U64:
        """Propose a snapshot off-chain vote.

        Args:
            proposer: Proposing address.
            ipfs_hash: IPFS URI of proposal content.
            duration: Voting duration in seconds.
        """
        self._require_initialized()
        proposer.require_auth()

        proposal_id = self.storage.get("proposal_count") + U64(1)
        self.storage.set("proposal_count", proposal_id)

        now = self.env.ledger().timestamp()
        vote_end = now + duration

        proposal = {
            "id": proposal_id,
            "proposer": proposer,
            "ipfs_hash": ipfs_hash,
            "vote_end": vote_end,
            "merkle_root": Bytes(b""), # Posted by relayer after vote finishes
            "yes_votes": U128(0),
            "no_votes": U128(0),
            "results_posted": False,
        }

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("proposal_created", {
            "proposal_id": proposal_id,
            "proposer": proposer,
            "ipfs_hash": ipfs_hash,
        })

        return proposal_id

    @external
    def relay_vote_signature(
        self,
        relayer: Address,
        proposal_id: U64,
        voter: Address,
        support: Bool,
        weight: U128,
        signature: Bytes,
    ):
        """Gasless Relay: Submits a voter's signed vote signature. Relayer pays gas.

        Args:
            relayer: Must be whitelisted relayer.
            proposal_id: ID of the proposal.
            voter: Voter address.
            support: True for yes, False for no.
            weight: Voter balance weight.
            signature: EIP-712 / Stellar signature of the voter.
        """
        self._require_initialized()
        relayer.require_auth()

        whitelisted_relayer = self.storage.get("relayer")
        if relayer != whitelisted_relayer:
            raise ContractError.UNAUTHORIZED

        proposal = self._get_proposal(proposal_id)
        now = self.env.ledger().timestamp()
        if now >= proposal["vote_end"]:
            raise ContractError.VOTING_PERIOD_ENDED

        already_voted = self.storage.get(("voted", proposal_id, voter), False)
        if already_voted:
            raise ContractError.ALREADY_VOTED

        # Verify signature length is 64 bytes (simulating ed25519 signature verification)
        if signature.length() != 64 and signature.length() != 65:
            # A real verification would use crypto verify, but signature sizes must match
            raise ContractError.INVALID_SIGNATURE

        # Register vote
        self.storage.set(("voted", proposal_id, voter), True)

        if support:
            proposal["yes_votes"] = proposal["yes_votes"] + weight
        else:
            proposal["no_votes"] = proposal["no_votes"] + weight

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("vote_relayed", {
            "proposal_id": proposal_id,
            "voter": voter,
            "support": support,
            "weight": weight,
        })

    @external
    def post_voting_result(
        self,
        relayer: Address,
        proposal_id: U64,
        merkle_root: Bytes,
        yes_votes: U128,
        no_votes: U128,
    ):
        """Submit the Merkle Root of passed execution actions. Only relayer.

        Args:
            relayer: Authorized relayer.
            proposal_id: Proposal ID.
            merkle_root: Merkle root of approved transactions.
            yes_votes: Final yes vote weight.
            no_votes: Final no vote weight.
        """
        self._require_initialized()
        relayer.require_auth()

        whitelisted_relayer = self.storage.get("relayer")
        if relayer != whitelisted_relayer:
            raise ContractError.UNAUTHORIZED

        proposal = self._get_proposal(proposal_id)
        now = self.env.ledger().timestamp()
        if now < proposal["vote_end"]:
            raise ContractError.VOTING_PERIOD_ACTIVE
        if proposal["results_posted"]:
            raise ContractError.ALREADY_EXECUTED

        proposal["merkle_root"] = merkle_root
        # Use maximum of relayed yes_votes or signature yes_votes
        if yes_votes > proposal["yes_votes"]:
            proposal["yes_votes"] = yes_votes
        if no_votes > proposal["no_votes"]:
            proposal["no_votes"] = no_votes

        proposal["results_posted"] = True
        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("results_posted", {
            "proposal_id": proposal_id,
            "merkle_root": merkle_root,
            "yes_votes": proposal["yes_votes"],
            "no_votes": proposal["no_votes"],
        })

    @external
    def execute_action(
        self,
        caller: Address,
        proposal_id: U64,
        target: Address,
        calldata: Bytes,
        proof: Vec,
    ):
        """Verify Merkle Proof and execute the approved off-chain action.

        Args:
            caller: Any trigger address.
            proposal_id: Proposal ID.
            target: Execution target.
            calldata: Execution calldata payload.
            proof: Merkle proof.
        """
        self._require_initialized()
        caller.require_auth()

        proposal = self._get_proposal(proposal_id)
        if not proposal["results_posted"]:
            raise ContractError.PROPOSAL_NOT_FOUND

        if proposal["yes_votes"] <= proposal["no_votes"]:
            raise ContractError.PROPOSAL_DEFEATED

        # Build leaf: hash(proposal_id + target + calldata)
        p_bytes = self.env.serialize(proposal_id)
        t_bytes = self.env.serialize(target)
        c_bytes = self.env.serialize(calldata)
        leaf = self.env.crypto().sha256(p_bytes + t_bytes + c_bytes)

        # Check if already executed
        if self.storage.get(("executed_action", proposal_id, leaf), False):
            raise ContractError.ALREADY_EXECUTED

        # Verify proof against proposal's merkle root
        if not self._verify_proof(proof, proposal["merkle_root"], leaf):
            raise ContractError.INVALID_PROOF

        # Mark executed
        self.storage.set(("executed_action", proposal_id, leaf), True)

        # Invoke contract action
        success = self.env.invoke_contract(target, "execute", [calldata])
        if not success:
            self.storage.set(("executed_action", proposal_id, leaf), False)
            raise ContractError.INVALID_PROOF

        self.env.emit_event("action_executed", {
            "proposal_id": proposal_id,
            "target": target,
        })

    @external
    def update_relayer(self, admin: Address, new_relayer: Address):
        """Update authorized relayer address. Only admin."""
        self._require_initialized()
        self._require_admin(admin)

        self.storage.set("relayer", new_relayer)
        self.env.emit_event("relayer_updated", {"new_relayer": new_relayer})

    @view
    def get_proposal(self, proposal_id: U64) -> Map:
        """Get proposal details."""
        return self._get_proposal(proposal_id)

    @view
    def is_action_executed(self, proposal_id: U64, target: Address, calldata: Bytes) -> Bool:
        """Check if an action has already been executed on-chain."""
        p_bytes = self.env.serialize(proposal_id)
        t_bytes = self.env.serialize(target)
        c_bytes = self.env.serialize(calldata)
        leaf = self.env.crypto().sha256(p_bytes + t_bytes + c_bytes)
        return self.storage.get(("executed_action", proposal_id, leaf), False)

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

    def _verify_proof(self, proof: Vec, root: Bytes, leaf: Bytes) -> Bool:
        computed_hash = leaf

        for i in range(proof.len()):
            proof_element = proof.get(i)
            if computed_hash < proof_element:
                computed_hash = self.env.crypto().sha256(computed_hash + proof_element)
            else:
                computed_hash = self.env.crypto().sha256(proof_element + computed_hash)

        return computed_hash == root
