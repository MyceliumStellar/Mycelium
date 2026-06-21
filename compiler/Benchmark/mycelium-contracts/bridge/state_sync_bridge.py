"""
State Synchronization Bridge — Merkle root synchronization and verification.

Mycelium Smart Contract for Stellar. Anchor contract verifying state roots of
foreign blockchains. Relayers submit state roots verified by validator threshold
signatures. User contracts query and verify arbitrary key-value proofs against
these synchronized Merkle roots.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)

class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    PAUSED = 4
    INVALID_BLOCK_NUMBER = 5
    INVALID_SIGNATURE = 6
    THRESHOLD_NOT_MET = 7
    STATE_NOT_FOUND = 8
    INVALID_VALIDATOR_COUNT = 9
    REPLAYED_ROOT = 10

@contract
class StateSyncBridge:
    """
    Merkle Root State synchronization contract supporting cross-chain state proofs.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        recovery_signer: Address,
        validators: Vec,
        threshold: U64
    ):
        """Initialize configurations, validators registry, and emergency control keys."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if len(validators) == 0 or threshold == U64(0) or threshold > len(validators):
            raise ContractError.INVALID_VALIDATOR_COUNT

        self.storage.set("admin", admin)
        self.storage.set("recovery_signer", recovery_signer)
        self.storage.set("threshold", threshold)
        self.storage.set("validator_count", len(validators))
        self.storage.set("paused", False)

        for i in range(len(validators)):
            pubkey = validators.get(i)
            self.storage.set(f"validator_{i}", pubkey)
            self.storage.set(f"is_validator_{pubkey}", True)

        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "recovery_signer": recovery_signer,
            "threshold": threshold
        })

    @external
    def submit_state_root(
        self,
        source_chain: Bytes,
        root: Bytes,
        block_number: U64,
        timestamp: U64,
        signatures: Vec
    ):
        """
        Submit a new synchronized state root from a foreign chain.
        Requires verification by a threshold of validator signatures.
        """
        self._require_initialized()
        self._require_not_paused()

        # Enforce progressive block height updating
        latest_num = self.storage.get(f"latest_block_{source_chain}", U64(0))
        if block_number <= latest_num:
            raise ContractError.INVALID_BLOCK_NUMBER

        # Check duplicate roots
        if self.storage.get(f"state_root_{source_chain}_{block_number}") is not None:
            raise ContractError.REPLAYED_ROOT

        # Reconstruct signature payload: hash(source_chain + root + block_number + timestamp)
        message = self._construct_message(source_chain, root, block_number, timestamp)

        # Validate signatures
        threshold = self.storage.get("threshold", U64(0))
        valid_count = U64(0)
        used_validators = Map(self.env)
        val_count = self.storage.get("validator_count", U64(0))

        for i in range(len(signatures)):
            sig = signatures.get(i)
            matched = False

            for j in range(int(val_count)):
                val_pubkey = self.storage.get(f"validator_{j}")
                if used_validators.get(val_pubkey, False):
                    continue

                if self.env.crypto().verify_sig_ed25519(val_pubkey, message, sig):
                    used_validators.set(val_pubkey, True)
                    valid_count += U64(1)
                    matched = True
                    break

            if not matched:
                raise ContractError.INVALID_SIGNATURE

        if valid_count < threshold:
            raise ContractError.THRESHOLD_NOT_MET

        # Save the verified state root
        self.storage.set(f"state_root_{source_chain}_{block_number}", root)
        self.storage.set(f"state_timestamp_{source_chain}_{block_number}", timestamp)
        self.storage.set(f"latest_block_{source_chain}", block_number)

        self.env.emit_event("state_root_synchronized", {
            "source_chain": source_chain,
            "root": root,
            "block_number": block_number,
            "timestamp": timestamp
        })

    @external
    def rollback_state_root(self, caller: Address, source_chain: Bytes, target_block_number: U64):
        """
        Emergency rollback of state roots (Admin or Recovery Signer only).
        Reverts the latest synchronized block number.
        """
        caller.require_auth()
        self._require_initialized()

        # Check authority
        admin = self.storage.get("admin")
        recovery = self.storage.get("recovery_signer")
        if caller != admin and caller != recovery:
            raise ContractError.UNAUTHORIZED

        latest_num = self.storage.get(f"latest_block_{source_chain}", U64(0))
        if target_block_number >= latest_num:
            raise ContractError.INVALID_BLOCK_NUMBER

        # Clean all intermediate block roots
        for b in range(int(target_block_number + U64(1)), int(latest_num + U64(1))):
            self.storage.remove(f"state_root_{source_chain}_{b}")
            self.storage.remove(f"state_timestamp_{source_chain}_{b}")

        # Reset latest block tracker
        self.storage.set(f"latest_block_{source_chain}", target_block_number)

        self.env.emit_event("state_root_rolled_back", {
            "source_chain": source_chain,
            "rolled_back_to": target_block_number,
            "previous_latest": latest_num
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause state submission (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_latest_block_number(self, source_chain: Bytes) -> U64:
        """Query the latest synchronized block number for a foreign chain."""
        return self.storage.get(f"latest_block_{source_chain}", U64(0))

    @view
    def get_state_root(self, source_chain: Bytes, block_number: U64) -> Bytes:
        """Fetch the synchronized Merkle root for a specific block number."""
        return self.storage.get(f"state_root_{source_chain}_{block_number}")

    @view
    def verify_state_proof(
        self,
        source_chain: Bytes,
        block_number: U64,
        key: Bytes,
        value: Bytes,
        proof: Vec,          # Vec of Bytes
        path_directions: Vec # Vec of U64 (0 = left sibling, 1 = right sibling)
    ) -> Bool:
        """
        Verify an arbitrary state key-value pair against a synchronized Merkle root.
        """
        self._require_initialized()

        root = self.storage.get(f"state_root_{source_chain}_{block_number}")
        if root is None:
            return False

        # Compute leaf hash
        computed_hash = self.env.crypto().sha256(key + value)

        # Traverse up the Merkle tree
        for i in range(len(proof)):
            sibling = proof.get(i)
            direction = path_directions.get(i)

            if direction == U64(0):
                # Sibling on left
                computed_hash = self.env.crypto().sha256(sibling + computed_hash)
            else:
                # Sibling on right
                computed_hash = self.env.crypto().sha256(computed_hash + sibling)

        return computed_hash == root

    # --- INTERNAL HELPERS ---

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_not_paused(self):
        if self.storage.get("paused", False):
            raise ContractError.PAUSED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _construct_message(self, chain: Bytes, root: Bytes, block_num: U64, ts: U64) -> Bytes:
        payload = Bytes(self.env)
        payload.concat(chain)
        payload.concat(root)
        payload.concat(Bytes(self.env, str(block_num).encode("utf-8")))
        payload.concat(Bytes(self.env, str(ts).encode("utf-8")))
        return payload
