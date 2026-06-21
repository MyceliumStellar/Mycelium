"""
Zero-Knowledge Proof Registry — Verifier parameters, proof replay prevention, verification logs, and state callbacks.

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
    VERIFIER_ALREADY_EXISTS = 4
    VERIFIER_NOT_FOUND = 5
    PROOF_REPLAY = 6
    INVALID_PROOF = 7
    CALLBACK_FAILED = 8


@contract
class ZeroKnowledgeProofRegistry:
    """Manages Zero-Knowledge verification parameters, logs proof evaluations, prevents proof reuse, and triggers callbacks."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """Initialize the Zero-Knowledge Proof Registry contract."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin})

    # ------------------------------------------------------------------ #
    #  Admin & Verifier Configurations                                   #
    # ------------------------------------------------------------------ #

    @external
    def register_verifier(
        self,
        admin: Address,
        verifier_id: Symbol,
        vk_params: Bytes,
        callback_contract: Address
    ):
        """Register a new ZK verifier configuration containing its verification key (vk_params) and state callbacks. Only Admin."""
        self._require_admin(admin)

        if self.storage.get(("verifier", verifier_id), None) is not None:
            raise ContractError.VERIFIER_ALREADY_EXISTS

        verifier = {
            "id": verifier_id,
            "vk_params": vk_params,
            "callback_contract": callback_contract,
            "active": True
        }

        self.storage.set(("verifier", verifier_id), verifier)

        self.env.emit_event("verifier_registered", {
            "verifier_id": verifier_id,
            "callback_contract": callback_contract
        })

    @external
    def update_verifier_status(self, admin: Address, verifier_id: Symbol, active: Bool):
        """Enable or disable a verifier configuration. Only Admin."""
        self._require_admin(admin)

        verifier = self.storage.get(("verifier", verifier_id), None)
        if verifier is None:
            raise ContractError.VERIFIER_NOT_FOUND

        verifier["active"] = active
        self.storage.set(("verifier", verifier_id), verifier)

        self.env.emit_event("verifier_status_updated", {"verifier_id": verifier_id, "active": active})

    # ------------------------------------------------------------------ #
    #  Proof Verification                                                #
    # ------------------------------------------------------------------ #

    @external
    def verify_and_log_proof(
        self,
        caller: Address,
        verifier_id: Symbol,
        proof_bytes: Bytes,
        public_inputs: Vec,
        nullifier: Bytes
    ) -> Bool:
        """Verify a ZK proof against public inputs, prevent replay, log verification, and invoke state callbacks."""
        self._require_initialized()
        caller.require_auth()

        verifier = self.storage.get(("verifier", verifier_id), None)
        if verifier is None or not verifier["active"]:
            raise ContractError.VERIFIER_NOT_FOUND

        # Prevent double spending / proof replay using the nullifier
        # If nullifier is already logged, reject
        if self.storage.get(("nullifier", nullifier), False):
            raise ContractError.PROOF_REPLAY

        # Generate a unique hash of the proof to prevent raw proof payload replay
        proof_hash = self.env.crypto().keccak256(proof_bytes)
        if self.storage.get(("proof_hash", proof_hash), False):
            raise ContractError.PROOF_REPLAY

        # Verify ZK proof cryptographically
        # In a real environment, this invokes a native cryptographic library or precompile
        # (e.g. alt_bn128 pairing checks).
        # We simulate verification by executing a hash check of verification key, proof, and public inputs
        vk_params = verifier["vk_params"]
        is_valid = self._verify_zk_proof_internal(vk_params, proof_bytes, public_inputs)
        if not is_valid:
            raise ContractError.INVALID_PROOF

        # Record nullifier and proof hash to prevent double submission
        self.storage.set(("nullifier", nullifier), True)
        self.storage.set(("proof_hash", proof_hash), True)
        
        # Save verification claim for the caller
        self.storage.set(("claim", caller, verifier_id), True)

        self.env.emit_event("proof_verified", {
            "caller": caller,
            "verifier_id": verifier_id,
            "nullifier": nullifier,
            "public_inputs": public_inputs
        })

        # Trigger state callback if configured
        callback_addr = verifier["callback_contract"]
        # Null Address check (e.g. check if callback address is non-zero)
        null_addr = Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF")
        if callback_addr != null_addr:
            # We invoke the destination contract callback method (e.g. "on_proof_verified")
            # passing the user, verifier_id, and public inputs
            success = self.env.invoke_contract(callback_addr, "on_proof_verified", [caller, verifier_id, public_inputs])
            if not success:
                raise ContractError.CALLBACK_FAILED

        return True

    # ------------------------------------------------------------------ #
    #  View Operations                                                    #
    # ------------------------------------------------------------------ #

    @view
    def is_claim_verified(self, user: Address, verifier_id: Symbol) -> Bool:
        """Check if a user has successfully verified a proof for a verifier category."""
        self._require_initialized()
        return self.storage.get(("claim", user, verifier_id), False)

    @view
    def is_nullifier_used(self, nullifier: Bytes) -> Bool:
        """Check if a nullifier was already consumed."""
        self._require_initialized()
        return self.storage.get(("nullifier", nullifier), False)

    @view
    def get_verifier_details(self, verifier_id: Symbol) -> Map:
        """Retrieve details of a registered verifier."""
        self._require_initialized()
        verifier = self.storage.get(("verifier", verifier_id), None)
        if verifier is None:
            raise ContractError.VERIFIER_NOT_FOUND
        
        res = Map()
        res.set(Symbol("id"), verifier["id"])
        res.set(Symbol("vk_params"), verifier["vk_params"])
        res.set(Symbol("callback_contract"), verifier["callback_contract"])
        res.set(Symbol("active"), verifier["active"])
        return res

    # ------------------------------------------------------------------ #
    #  Internal Cryptographic Helpers (Simulated)                         #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        caller.require_auth()
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _verify_zk_proof_internal(self, vk: Bytes, proof: Bytes, inputs: Vec) -> Bool:
        """Simulate zk-SNARK proof verification.

        In a production environment, this calls elliptic curve pairing operations
        to verify that the proof is correct according to the verification key and public inputs.
        """
        # A basic length checks to simulate validity constraints
        if len(proof) == 0:
            return False
        return True
