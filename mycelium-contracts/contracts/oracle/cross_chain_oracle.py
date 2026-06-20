"""
Cross Chain Oracle — Foreign state Merkle root relay with operator bonds, disputes, and proof verification.

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
    INSUFFICIENT_BOND = 4
    ROOT_NOT_FOUND = 5
    INVALID_STATE = 6
    CHALLENGE_PERIOD_EXPIRED = 7
    CHALLENGE_PERIOD_ACTIVE = 8
    TRANSFER_FAILED = 9
    PROOF_VERIFICATION_FAILED = 10
    REENTRANT_CALL = 11


class RootStatus:
    SUBMITTED = 0
    CHALLENGED = 1
    FINALIZED = 2
    VETOED = 3


@contract
class CrossChainOracle:
    """Cross-Chain Oracle contract storing state roots of foreign networks,
    verifying cryptographic Merkle proofs, and handling relay bonds, disputes, and slashing."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        stake_token: Address,
        arbitrator: Address,
        relay_bond: U128,
        challenge_bond: U128,
        challenge_period: U64,
    ):
        """Initialize configurations."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("stake_token", stake_token)
        self.storage.set("arbitrator", arbitrator)
        self.storage.set("relay_bond", relay_bond)
        self.storage.set("challenge_bond", challenge_bond)
        self.storage.set("challenge_period", challenge_period)
        
        self.storage.set("execution_lock", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "arbitrator": arbitrator,
            "stake_token": stake_token,
            "challenge_period": challenge_period,
        })

    # ------------------------------------------------------------------ #
    #  Relay Operations                                                   #
    # ------------------------------------------------------------------ #

    @external
    def relay_root(
        self,
        operator: Address,
        chain_id: U64,
        block_number: U64,
        state_root: Bytes,
    ):
        """Relay a foreign chain state root. Requires staking the relay bond.

        Args:
            operator: The relay operator address.
            chain_id: Identifier of the source blockchain (e.g. 1 for Ethereum).
            block_number: The source block number.
            state_root: The state root bytes.
        """
        self._require_initialized()
        operator.require_auth()

        root_key = ("root", chain_id, block_number)
        existing = self.storage.get(root_key, None)
        if existing is not None and existing["status"] != RootStatus.VETOED:
            raise ContractError.INVALID_STATE

        # Charge relay bond
        bond = self.storage.get("relay_bond")
        stake_token = self.storage.get("stake_token")
        contract_addr = self.env.current_contract_address()
        if bond > U128(0):
            success = self.env.invoke_contract(stake_token, "transfer", [operator, contract_addr, bond])
            if not success:
                raise ContractError.TRANSFER_FAILED

        now = self.env.ledger().timestamp()
        challenge_period = self.storage.get("challenge_period")
        challenge_deadline = now + challenge_period

        root_data = {
            "chain_id": chain_id,
            "block_number": block_number,
            "root": state_root,
            "operator": operator,
            "bond_amount": bond,
            "submitted_at": now,
            "challenge_deadline": challenge_deadline,
            "status": RootStatus.SUBMITTED,
            "challenger": operator, # placeholder
            "challenge_bond": U128(0),
        }

        self.storage.set(root_key, root_data)

        self.env.emit_event("root_relayed", {
            "chain_id": chain_id,
            "block_number": block_number,
            "root": state_root,
            "operator": operator,
            "challenge_deadline": challenge_deadline,
        })

    # ------------------------------------------------------------------ #
    #  Disputes & Challenges                                              #
    # ------------------------------------------------------------------ #

    @external
    def challenge_root(self, challenger: Address, chain_id: U64, block_number: U64):
        """Challenge a submitted state root before the challenge period ends.

        Args:
            challenger: Address challenging the root.
            chain_id: Source chain ID.
            block_number: Target block number.
        """
        self._require_initialized()
        challenger.require_auth()

        root_key = ("root", chain_id, block_number)
        root_data = self.storage.get(root_key, None)
        if root_data is None:
            raise ContractError.ROOT_NOT_FOUND

        if root_data["status"] != RootStatus.SUBMITTED:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        if now > root_data["challenge_deadline"]:
            raise ContractError.CHALLENGE_PERIOD_EXPIRED

        # Charge challenge bond
        bond = self.storage.get("challenge_bond")
        stake_token = self.storage.get("stake_token")
        contract_addr = self.env.current_contract_address()
        if bond > U128(0):
            success = self.env.invoke_contract(stake_token, "transfer", [challenger, contract_addr, bond])
            if not success:
                raise ContractError.TRANSFER_FAILED

        root_data["status"] = RootStatus.CHALLENGED
        root_data["challenger"] = challenger
        root_data["challenge_bond"] = bond

        self.storage.set(root_key, root_data)

        self.env.emit_event("root_challenged", {
            "chain_id": chain_id,
            "block_number": block_number,
            "challenger": challenger,
            "bond": bond,
        })

    @external
    def resolve_dispute(
        self,
        arbitrator: Address,
        chain_id: U64,
        block_number: U64,
        is_root_valid: Bool,
    ):
        """Resolve a disputed root. Only designated Arbitrator.

        Args:
            arbitrator: Arbitrator address.
            chain_id: Chain ID.
            block_number: Block number.
            is_root_valid: True to validate the operator's root, False to veto it and uphold dispute.
        """
        self._require_initialized()
        arbitrator.require_auth()
        self._require_arbitrator(arbitrator)
        self._require_no_reentrant()

        root_key = ("root", chain_id, block_number)
        root_data = self.storage.get(root_key, None)
        if root_data is None:
            raise ContractError.ROOT_NOT_FOUND

        if root_data["status"] != RootStatus.CHALLENGED:
            raise ContractError.INVALID_STATE

        operator = root_data["operator"]
        challenger = root_data["challenger"]
        op_bond = root_data["bond_amount"]
        chal_bond = root_data["challenge_bond"]
        
        stake_token = self.storage.get("stake_token")
        contract_addr = self.env.current_contract_address()

        if is_root_valid:
            # Operator was correct!
            root_data["status"] = RootStatus.FINALIZED
            
            # Refund operator bond
            if op_bond > U128(0):
                self.env.invoke_contract(stake_token, "transfer", [contract_addr, operator, op_bond])
            
            # Slash challenger bond: reward to operator
            if chal_bond > U128(0):
                self.env.invoke_contract(stake_token, "transfer", [contract_addr, operator, chal_bond])
        else:
            # Challenger was correct! Operator root vetoed
            root_data["status"] = RootStatus.VETOED
            
            # Refund challenger bond
            if chal_bond > U128(0):
                self.env.invoke_contract(stake_token, "transfer", [contract_addr, challenger, chal_bond])
            
            # Slash operator bond: reward to challenger
            if op_bond > U128(0):
                self.env.invoke_contract(stake_token, "transfer", [contract_addr, challenger, op_bond])

        self.storage.set(root_key, root_data)

        self.env.emit_event("root_dispute_resolved", {
            "chain_id": chain_id,
            "block_number": block_number,
            "is_root_valid": is_root_valid,
        })

    @external
    def finalize_root(self, caller: Address, chain_id: U64, block_number: U64):
        """Finalize root if challenge period has passed. Proposer bond is returned.

        Args:
            caller: Any address.
            chain_id: Chain ID.
            block_number: Block number.
        """
        self._require_initialized()
        caller.require_auth()

        root_key = ("root", chain_id, block_number)
        root_data = self.storage.get(root_key, None)
        if root_data is None:
            raise ContractError.ROOT_NOT_FOUND

        if root_data["status"] != RootStatus.SUBMITTED:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        if now <= root_data["challenge_deadline"]:
            raise ContractError.CHALLENGE_PERIOD_ACTIVE

        # Finalize root
        root_data["status"] = RootStatus.FINALIZED
        self.storage.set(root_key, root_data)

        # Refund operator bond
        op_bond = root_data["bond_amount"]
        if op_bond > U128(0):
            stake_token = self.storage.get("stake_token")
            contract_addr = self.env.current_contract_address()
            self.env.invoke_contract(stake_token, "transfer", [contract_addr, root_data["operator"], op_bond])

        self.env.emit_event("root_finalized", {
            "chain_id": chain_id,
            "block_number": block_number,
            "root": root_data["root"],
        })

    # ------------------------------------------------------------------ #
    #  Merkle Proof Validation (View)                                     #
    # ------------------------------------------------------------------ #

    @view
    def verify_state_leaf(
        self,
        chain_id: U64,
        block_number: U64,
        leaf: Bytes,
        proof: Vec,
        index: U64,
    ) -> Bool:
        """Verify that a leaf is part of the finalized state root using a Merkle proof.

        Args:
            chain_id: Chain ID.
            block_number: Target block number (root must be finalized).
            leaf: Leaf hash.
            proof: Vector of sibling hashes.
            index: Leaf index in the tree.
        """
        self._require_initialized()
        root_key = ("root", chain_id, block_number)
        root_data = self.storage.get(root_key, None)
        if root_data is None or root_data["status"] != RootStatus.FINALIZED:
            raise ContractError.ROOT_NOT_FOUND

        # Validate on-chain Merkle proof
        computed_root = self._verify_merkle(leaf, proof, index)
        return computed_root == root_data["root"]

    # ------------------------------------------------------------------ #
    #  Admin Configurations                                               #
    # ------------------------------------------------------------------ #

    @external
    def update_config(
        self,
        admin: Address,
        arbitrator: Address,
        relay_bond: U128,
        challenge_bond: U128,
        challenge_period: U64,
    ):
        """Update configurations. Only Admin."""
        self._require_admin(admin)
        self.storage.set("arbitrator", arbitrator)
        self.storage.set("relay_bond", relay_bond)
        self.storage.set("challenge_bond", challenge_bond)
        self.storage.set("challenge_period", challenge_period)
        self.env.emit_event("config_updated", {
            "arbitrator": arbitrator,
            "relay_bond": relay_bond,
            "challenge_bond": challenge_bond,
        })

    @external
    def transfer_admin(self, admin: Address, new_admin: Address):
        """Transfer admin role. Only Admin."""
        self._require_admin(admin)
        self.storage.set("admin", new_admin)
        self.env.emit_event("admin_transferred", {"old_admin": admin, "new_admin": new_admin})

    # ------------------------------------------------------------------ #
    #  View Functions                                                     #
    # ------------------------------------------------------------------ #

    @view
    def get_root_data(self, chain_id: U64, block_number: U64) -> Map:
        """Get root entry details."""
        self._require_initialized()
        root_key = ("root", chain_id, block_number)
        data = self.storage.get(root_key, None)
        if data is None:
            raise ContractError.ROOT_NOT_FOUND
        return data

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

    def _require_arbitrator(self, caller: Address):
        arb = self.storage.get("arbitrator")
        if caller != arb:
            raise ContractError.UNAUTHORIZED

    def _require_no_reentrant(self):
        if self.storage.get("execution_lock", False):
            raise ContractError.REENTRANT_CALL

    def _verify_merkle(self, leaf: Bytes, proof: Vec, index: U64) -> Bytes:
        """Helper to reconstruct the root from a leaf, proof, and index path."""
        temp = leaf
        path = index
        for i in range(len(proof)):
            sibling = proof[i]
            # If path is odd, leaf is on the right, sibling is on the left
            if path % U64(2) == U64(1):
                temp = self.env.crypto().keccak256(sibling, temp)
            else:
                temp = self.env.crypto().keccak256(temp, sibling)
            path = path / U64(2)
        return temp
