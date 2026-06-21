"""
Batch Payment — Merkle-tree token distribution contract with expiration refunds.

Mycelium Smart Contract for Stellar
Allows administering batch token distributions (drops) in epochs. Beneficiaries can claim
their allocations by presenting a Merkle proof. Admin can recall unclaimed expired funds
once an epoch's expiration timestamp is passed.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    INVALID_PARAMETERS = 4
    INVALID_PROOF = 5
    ALREADY_CLAIMED = 6
    DISTRIBUTION_EXPIRED = 7
    DISTRIBUTION_NOT_EXPIRED = 8
    ALREADY_REFUNDED = 9
    EPOCH_NOT_FOUND = 10
    INSUFFICIENT_BALANCE = 11


@contract
class BatchPayment:
    """
    Epoch-based Merkle distribution engine enabling gas-efficient multi-party payments
    with expiration-guarded administrative clawbacks.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, token: Address):
        """Initialize the batch payment distribution contract."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("token", token)
        self.storage.set("current_epoch", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "token": token,
        })

    @external
    def start_distribution_epoch(
        self,
        admin: Address,
        merkle_root: Bytes,
        total_allocation: U128,
        duration_seconds: U64,
    ) -> U64:
        """Start a new distribution epoch, requiring admin to deposit the total allocation."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        if total_allocation == 0 or duration_seconds == 0:
            raise ContractError.INVALID_PARAMETERS

        # Transfer tokens from admin to contract
        token = self.storage.get("token")
        self.env.transfer(token, admin, self.env.current_contract(), total_allocation)

        current_epoch = self.storage.get("current_epoch", U64(0))
        new_epoch = current_epoch + U64(1)
        self.storage.set("current_epoch", new_epoch)

        current_time = self.env.ledger().timestamp()
        expiration = current_time + duration_seconds

        self.storage.set(f"merkle_root:{new_epoch}", merkle_root)
        self.storage.set(f"expiration:{new_epoch}", expiration)
        self.storage.set(f"total_allocated:{new_epoch}", total_allocation)
        self.storage.set(f"total_claimed:{new_epoch}", U128(0))
        self.storage.set(f"refunded:{new_epoch}", False)

        self.env.emit_event("epoch_started", {
            "epoch": new_epoch,
            "merkle_root": merkle_root,
            "allocation": total_allocation,
            "expiration": expiration,
        })

        return new_epoch

    @external
    def claim(
        self,
        user: Address,
        epoch: U64,
        amount: U128,
        proof: Vec,
    ):
        """Claim tokens allocated in a specific distribution epoch by proving membership in the Merkle tree."""
        user.require_auth()
        self._require_initialized()

        self._check_epoch_exists(epoch)

        # Check expiration
        expiration = self.storage.get(f"expiration:{epoch}")
        current_time = self.env.ledger().timestamp()
        if current_time >= expiration:
            raise ContractError.DISTRIBUTION_EXPIRED

        # Check claimed state
        if self.storage.get(f"claimed:{epoch}:{user}", False):
            raise ContractError.ALREADY_CLAIMED

        merkle_root = self.storage.get(f"merkle_root:{epoch}")

        # Compute Merkle leaf hash: sha256(serialize(user) + serialize(amount))
        user_bytes = self.env.serialize(user)
        amount_bytes = self.env.serialize(amount)
        leaf = self.env.crypto().sha256(user_bytes + amount_bytes)

        # Verify Merkle proof
        if not self._verify_proof(proof, merkle_root, leaf):
            raise ContractError.INVALID_PROOF

        # Check contract balance safety (in case of double allocation issues in the tree)
        total_allocated = self.storage.get(f"total_allocated:{epoch}")
        total_claimed = self.storage.get(f"total_claimed:{epoch}", U128(0))
        if total_claimed + amount > total_allocated:
            raise ContractError.INSUFFICIENT_BALANCE

        # Update states
        self.storage.set(f"claimed:{epoch}:{user}", True)
        self.storage.set(f"total_claimed:{epoch}", total_claimed + amount)

        # Transfer payout
        token = self.storage.get("token")
        self.env.transfer(token, self.env.current_contract(), user, amount)

        self.env.emit_event("claimed", {
            "user": user,
            "epoch": epoch,
            "amount": amount,
        })

    @external
    def refund_expired_tokens(self, admin: Address, epoch: U64, recipient: Address):
        """Allows admin to reclaim all unclaimed tokens of an epoch after it has expired."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        self._check_epoch_exists(epoch)

        if self.storage.get(f"refunded:{epoch}", False):
            raise ContractError.ALREADY_REFUNDED

        # Ensure epoch has expired
        expiration = self.storage.get(f"expiration:{epoch}")
        current_time = self.env.ledger().timestamp()
        if current_time < expiration:
            raise ContractError.DISTRIBUTION_NOT_EXPIRED

        total_allocated = self.storage.get(f"total_allocated:{epoch}")
        total_claimed = self.storage.get(f"total_claimed:{epoch}")
        
        unclaimed = total_allocated - total_claimed

        self.storage.set(f"refunded:{epoch}", True)

        if unclaimed > 0:
            token = self.storage.get("token")
            self.env.transfer(token, self.env.current_contract(), recipient, unclaimed)

        self.env.emit_event("epoch_refunded", {
            "epoch": epoch,
            "recipient": recipient,
            "amount": unclaimed,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_epoch_details(self, epoch: U64) -> Map:
        """Query parameters of a distribution epoch."""
        self._check_epoch_exists(epoch)
        return {
            "merkle_root": self.storage.get(f"merkle_root:{epoch}"),
            "expiration": self.storage.get(f"expiration:{epoch}"),
            "total_allocated": self.storage.get(f"total_allocated:{epoch}"),
            "total_claimed": self.storage.get(f"total_claimed:{epoch}"),
            "refunded": self.storage.get(f"refunded:{epoch}"),
        }

    @view
    def has_claimed(self, user: Address, epoch: U64) -> Bool:
        """Check if a user has claimed their allocation for a given epoch."""
        return self.storage.get(f"claimed:{epoch}:{user}", False)

    @view
    def get_current_epoch(self) -> U64:
        """Query the latest active/created epoch ID."""
        return self.storage.get("current_epoch", U64(0))

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _check_epoch_exists(self, epoch: U64):
        current_epoch = self.storage.get("current_epoch", U64(0))
        if epoch == 0 or epoch > current_epoch:
            raise ContractError.EPOCH_NOT_FOUND

    def _verify_proof(self, proof: Vec, root: Bytes, leaf: Bytes) -> Bool:
        """Verifies a Merkle proof proving that a leaf hash is in a Merkle tree with a given root."""
        computed_hash = leaf

        for i in range(len(proof)):
            proof_element = proof[i]
            # Standard sorting logic to compute parent hash
            if computed_hash < proof_element:
                computed_hash = self.env.crypto().sha256(computed_hash + proof_element)
            else:
                computed_hash = self.env.crypto().sha256(proof_element + computed_hash)

        return computed_hash == root
