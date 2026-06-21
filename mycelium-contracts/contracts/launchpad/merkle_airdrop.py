"""
Merkle Airdrop — Gasless Merkle tree claim verification, expiration limits, refund of unclaimed.

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
    AIRDROP_EXPIRED = 4
    ALREADY_CLAIMED = 5
    INVALID_PROOF = 6
    ZERO_AMOUNT = 7
    INSUFFICIENT_BALANCE = 8
    NOT_EXPIRED = 9


@contract
class MerkleAirdrop:
    """A gas-efficient Merkle Airdrop contract verifying claimants via cryptographic proofs."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        token: Address,
        merkle_root: Bytes,
        expiration_time: U64,
    ):
        """Initialize the Merkle Airdrop contract.

        Args:
            admin: Admin address.
            token: Airdrop token address.
            merkle_root: 32-byte Merkle root hash.
            expiration_time: Timestamp after which claims are disabled.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("token", token)
        self.storage.set("merkle_root", merkle_root)
        self.storage.set("expiration_time", expiration_time)

        self.storage.set("total_claimed", U128(0))
        self.storage.set("refunded", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "token": token,
            "expiration": expiration_time,
        })

    @external
    def claim(
        self,
        claimant: Address,
        amount: U128,
        proof: Vec, # Vector of 32-byte sibling hashes
    ) -> U128:
        """Claim airdropped tokens by submitting a valid Merkle proof.

        Args:
            claimant: Address claiming the tokens.
            amount: Token amount.
            proof: Cryptographic Merkle proof.
        """
        self._require_initialized()
        claimant.require_auth()

        now = self.env.ledger().timestamp()
        if now >= self.storage.get("expiration_time"):
            raise ContractError.AIRDROP_EXPIRED

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        if self.storage.get(("claimed", claimant), False):
            raise ContractError.ALREADY_CLAIMED

        # Compute leaf hash: sha256(claimant + amount)
        leaf = self._compute_leaf(claimant, amount)

        # Verify Merkle proof
        root = self.storage.get("merkle_root")
        if not self._verify_proof(proof, root, leaf):
            raise ContractError.INVALID_PROOF

        self.storage.set(("claimed", claimant), True)

        total_claimed = self.storage.get("total_claimed")
        self.storage.set("total_claimed", total_claimed + amount)

        token = self.storage.get("token")
        self.env.invoke_contract(
            token,
            "transfer",
            [self.env.current_contract_address(), claimant, amount]
        )

        self.env.emit_event("claimed", {
            "claimant": claimant,
            "amount": amount,
        })

        return amount

    @external
    def reclaim_unclaimed(self, admin: Address) -> U128:
        """Reclaim all remaining unclaimed tokens in the contract. Only admin, after expiration.

        Args:
            admin: Admin address.
        """
        self._require_initialized()
        admin.require_auth()

        expected_admin = self.storage.get("admin")
        if admin != expected_admin:
            raise ContractError.UNAUTHORIZED

        now = self.env.ledger().timestamp()
        if now < self.storage.get("expiration_time"):
            raise ContractError.NOT_EXPIRED

        if self.storage.get("refunded", False):
            raise ContractError.ALREADY_CLAIMED

        self.storage.set("refunded", True)

        token = self.storage.get("token")
        
        # Withdraw the full remaining token balance
        # In a real environment, we'd query the balance, but to be robust,
        # we can execute a transfer of the remaining tokens.
        # Since we don't have a direct balanceOf query in storage, we can fetch
        # it by calling the token contract's balance view.
        balance = self.env.invoke_contract(token, "balance", [self.env.current_contract_address()])
        
        if balance > U128(0):
            self.env.invoke_contract(
                token,
                "transfer",
                [self.env.current_contract_address(), admin, balance]
            )

        self.env.emit_event("unclaimed_reclaimed", {
            "admin": admin,
            "amount": balance,
        })

        return balance

    @view
    def is_claimed(self, user: Address) -> Bool:
        """Check if a user has claimed their airdrop."""
        return self.storage.get(("claimed", user), False)

    @view
    def get_status(self) -> Map:
        """Get status details of the airdrop."""
        res = Map()
        res.set("total_claimed", self.storage.get("total_claimed"))
        res.set("expiration_time", self.storage.get("expiration_time"))
        res.set("refunded", self.storage.get("refunded"))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _compute_leaf(self, claimant: Address, amount: U128) -> Bytes:
        # Standardize serialization for hashing
        # In Soroban / Mycelium, we can construct raw bytes
        # Let's concatenate claimant string representation and U128 serialized representation
        claimant_bytes = claimant.to_string().to_bytes()
        amount_bytes = self._u128_to_bytes(amount)
        
        # Combine bytes
        # Standard addition of bytes: claimant_bytes + amount_bytes
        # Then SHA-256 hash it
        combined = claimant_bytes + amount_bytes
        return self.env.crypto().sha256(combined)

    def _u128_to_bytes(self, val: U128) -> Bytes:
        # Convert U128 to a 16-byte array
        # Create a Python list and fill with bytes
        # Since we need to return Bytes, we can convert list of integers
        res_list = []
        temp = val
        for _ in range(16):
            res_list.append(int(temp % U128(256)))
            temp = temp / U128(256)
        
        # In Mycelium, we can instantiate Bytes from a python list or bytes type
        # Or construct via standard library. Let's return bytes(res_list) or similar.
        # The compiler compiles python types to WASM. So bytes(res_list) is standard.
        return bytes(res_list)

    def _verify_proof(self, proof: Vec, root: Bytes, leaf: Bytes) -> Bool:
        computed_hash = leaf
        
        for i in range(len(proof)):
            proof_element = proof.get(i)
            # Compare byte arrays lexicographically to compute canonical Merkle tree node
            if computed_hash < proof_element:
                combined = computed_hash + proof_element
            else:
                combined = proof_element + computed_hash
            
            computed_hash = self.env.crypto().sha256(combined)
            
        return computed_hash == root
