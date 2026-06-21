"""
MerkleStakingRewards — Merkle-tree rewards claiming contract.

Mycelium Smart Contract for Stellar
Allows users to claim their staking rewards using Merkle proofs verified on-chain.
Maintains cumulative claims per epoch to prevent double-claiming.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


# ── Error Codes ──────────────────────────────────────────────────────────────

class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    INVALID_PROOF = 4
    ALREADY_CLAIMED = 5
    EPOCH_NOT_FOUND = 6
    INSUFFICIENT_POOL = 7
    INVALID_EPOCH = 8
    ZERO_AMOUNT = 9


@contract
class MerkleStakingRewards:
    """
    Staking rewards distribution contract that verifies off-chain calculated rewards
    using an epoch-based Merkle root system.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ── Admin Operations ─────────────────────────────────────────────────

    @external
    def initialize(self, admin: Address, reward_token: Address):
        """
        One-time initialization. Sets admin and reward token.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("reward_token", reward_token)
        self.storage.set("reward_pool", U128(0))
        self.storage.set("current_epoch", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "reward_token": reward_token,
        })

    @external
    def post_merkle_root(self, caller: Address, epoch: U64, merkle_root: Bytes):
        """
        Admin-only: post the Merkle root for a specific epoch.
        Allows updating or posting a new epoch.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        current_epoch = self.storage.get("current_epoch")
        
        # Ensure we are either posting the next epoch or updating the current one
        if epoch > current_epoch + U64(1):
            raise ContractError.INVALID_EPOCH

        self.storage.set(f"merkle_root:{epoch}", merkle_root)

        if epoch > current_epoch:
            self.storage.set("current_epoch", epoch)

        self.env.emit_event("merkle_root_posted", {
            "epoch": epoch,
            "merkle_root": merkle_root,
        })

    @external
    def top_up_rewards(self, caller: Address, amount: U128):
        """
        Admin-only: Deposit reward tokens into the contract reward pool.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        reward_token = self.storage.get("reward_token")
        self.env.transfer(caller, self.env.current_contract(), reward_token, amount)

        pool = self.storage.get("reward_pool")
        self.storage.set("reward_pool", pool + amount)

        self.env.emit_event("rewards_topped_up", {
            "amount": amount,
            "new_pool": pool + amount,
        })

    # ── User Actions ─────────────────────────────────────────────────────

    @external
    def claim(
        self,
        user: Address,
        epoch: U64,
        cumulative_amount: U128,
        proof: Vec,
    ):
        """
        Claim rewards for a specific epoch by providing a Merkle proof.
        Claims are cumulative: users claim `cumulative_amount - already_claimed`.
        """
        user.require_auth()
        self._require_initialized()

        merkle_root = self.storage.get(f"merkle_root:{epoch}", None)
        if merkle_root is None:
            raise ContractError.EPOCH_NOT_FOUND

        # Generate leaf hash: sha256(serialize(user) + serialize(cumulative_amount))
        # Soroban/Mycelium serialize converts variables to standard binary XDR representations
        user_bytes = self.env.serialize(user)
        amount_bytes = self.env.serialize(cumulative_amount)
        leaf = self.env.crypto().sha256(user_bytes + amount_bytes)

        # Verify Merkle Proof
        if not self._verify_proof(proof, merkle_root, leaf):
            raise ContractError.INVALID_PROOF

        # Calculate payout: cumulative reward for epoch minus what user already claimed
        already_claimed = self.storage.get(f"claimed:{user}:{epoch}", U128(0))
        if cumulative_amount <= already_claimed:
            raise ContractError.ALREADY_CLAIMED

        payout = cumulative_amount - already_claimed

        # Ensure reward pool has enough tokens
        pool = self.storage.get("reward_pool")
        if payout > pool:
            raise ContractError.INSUFFICIENT_POOL

        # Update states
        self.storage.set(f"claimed:{user}:{epoch}", cumulative_amount)
        self.storage.set("reward_pool", pool - payout)

        # Transfer rewards
        reward_token = self.storage.get("reward_token")
        self.env.transfer(self.env.current_contract(), user, reward_token, payout)

        self.env.emit_event("rewards_claimed", {
            "user": user,
            "epoch": epoch,
            "payout": payout,
            "cumulative": cumulative_amount,
        })

    # ── View Functions ───────────────────────────────────────────────────

    @view
    def get_claimed(self, user: Address, epoch: U64) -> U128:
        """
        Get the cumulative reward amount already claimed by a user for an epoch.
        """
        return self.storage.get(f"claimed:{user}:{epoch}", U128(0))

    @view
    def get_merkle_root(self, epoch: U64) -> Bytes:
        """
        Get the Merkle root hash for a given epoch.
        """
        root = self.storage.get(f"merkle_root:{epoch}", None)
        if root is None:
            raise ContractError.EPOCH_NOT_FOUND
        return root

    @view
    def get_current_epoch(self) -> U64:
        """
        Get the latest active epoch.
        """
        return self.storage.get("current_epoch", U64(0))

    @view
    def get_reward_pool_balance(self) -> U128:
        """
        Get remaining rewards in pool.
        """
        return self.storage.get("reward_pool", U128(0))

    # ── Internal Helpers ─────────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _verify_proof(self, proof: Vec, root: Bytes, leaf: Bytes) -> Bool:
        """
        Verifies a Merkle proof proving that a leaf hash is in a Merkle tree with a given root.
        """
        computed_hash = leaf

        for i in range(proof.len()):
            proof_element = proof.get(i)
            # Sort to keep order consistent during hashing (standard OpenZeppelin MerkleProof logic)
            if computed_hash < proof_element:
                computed_hash = self.env.crypto().sha256(computed_hash + proof_element)
            else:
                computed_hash = self.env.crypto().sha256(proof_element + computed_hash)

        return computed_hash == root
