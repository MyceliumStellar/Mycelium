"""
NFT Bridge — Cross-chain non-fungible token transfers and metadata lockers.

Mycelium Smart Contract for Stellar. Locks native NFTs in a local locker and emits
bridge-out events. Mint and burn operations are performed on wrapper NFT 
collections representing foreign NFTs, authorized by validator signatures.
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
    INVALID_VALIDATOR_COUNT = 5
    INVALID_SIGNATURE = 6
    THRESHOLD_NOT_MET = 7
    REPLAYED_TX = 8
    COLLECTION_NOT_REGISTERED = 9
    NFT_TRANSFER_FAILED = 10

@contract
class NFTBridge:
    """
    NFT bridge managing metadata locking and wrapper NFT minting.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        validators: Vec,
        threshold: U64
    ):
        """Initialize validator parameters and admin controls."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if len(validators) == 0 or threshold == U64(0) or threshold > len(validators):
            raise ContractError.INVALID_VALIDATOR_COUNT

        self.storage.set("admin", admin)
        self.storage.set("threshold", threshold)
        self.storage.set("validator_count", len(validators))
        self.storage.set("lock_nonce", U64(1))
        self.storage.set("paused", False)

        for i in range(len(validators)):
            pubkey = validators.get(i)
            self.storage.set(f"validator_{i}", pubkey)
            self.storage.set(f"is_validator_{pubkey}", True)

        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "threshold": threshold
        })

    @external
    def register_collection_map(
        self,
        caller: Address,
        local_collection: Address,
        foreign_chain: Bytes,
        foreign_collection: Bytes,
        is_wrapper: Bool # True if local collection is a wrapper minted here
    ):
        """
        Map a local NFT contract address to a foreign chain NFT contract.
        - is_wrapper: True means local NFT is a wrapper minted by this bridge.
                      False means local NFT is a native NFT locked in this bridge.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        self.storage.set(f"local_to_foreign_{local_collection}", foreign_collection)
        self.storage.set(f"foreign_to_local_{foreign_chain}_{foreign_collection}", local_collection)
        self.storage.set(f"is_wrapper_{local_collection}", is_wrapper)

        self.env.emit_event("collection_mapped", {
            "local_collection": local_collection,
            "foreign_chain": foreign_chain,
            "foreign_collection": foreign_collection,
            "is_wrapper": is_wrapper
        })

    @external
    def lock_nft(
        self,
        caller: Address,
        local_collection: Address,
        token_id: U128,
        foreign_recipient: Bytes
    ):
        """
        Lock a native local NFT to bridge it out to a foreign chain.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        # Check that this is a locked native asset collection, not a wrapper collection
        if self.storage.get(f"is_wrapper_{local_collection}", False):
            raise ContractError.COLLECTION_NOT_REGISTERED

        foreign_collection = self.storage.get(f"local_to_foreign_{local_collection}")
        if foreign_collection is None:
            raise ContractError.COLLECTION_NOT_REGISTERED

        # Transfer the NFT to this contract
        # Standard NFT transfer signature: transfer(from, to, token_id)
        contract_addr = self.env.current_contract_address()
        self.env.call(local_collection, "transfer", caller, contract_addr, token_id)

        nonce = self.storage.get("lock_nonce", U64(1))
        self.storage.set("lock_nonce", nonce + U64(1))

        self.env.emit_event("nft_locked", {
            "local_collection": local_collection,
            "token_id": token_id,
            "sender": caller,
            "foreign_collection": foreign_collection,
            "foreign_recipient": foreign_recipient,
            "nonce": nonce
        })

    @external
    def release_nft(
        self,
        recipient: Address,
        local_collection: Address,
        token_id: U128,
        source_tx_hash: Bytes,
        signatures: Vec
    ):
        """
        Release a native locked NFT back to the local owner (bridge in).
        Authorized by validator signature threshold.
        """
        self._require_initialized()
        self._require_not_paused()

        if self.storage.get(f"processed_tx_{source_tx_hash}", False):
            raise ContractError.REPLAYED_TX

        # Ensure collection is registered and is native (not wrapper)
        if self.storage.get(f"is_wrapper_{local_collection}", False):
            raise ContractError.COLLECTION_NOT_REGISTERED

        # Re-construct signature message: hash(recipient + local_collection + token_id + source_tx_hash)
        message = self._construct_message(recipient, local_collection, token_id, source_tx_hash)

        # Verify validator signatures
        self._verify_signatures(message, signatures)

        # Mark processed
        self.storage.set(f"processed_tx_{source_tx_hash}", True)

        # Transfer NFT from contract to recipient
        contract_addr = self.env.current_contract_address()
        self.env.call(local_collection, "transfer", contract_addr, recipient, token_id)

        self.env.emit_event("nft_released", {
            "local_collection": local_collection,
            "token_id": token_id,
            "recipient": recipient,
            "source_tx_hash": source_tx_hash
        })

    @external
    def burn_wrapper_nft(
        self,
        caller: Address,
        local_collection: Address,
        token_id: U128,
        foreign_recipient: Bytes
    ):
        """
        Burn a local wrapper NFT to claim the corresponding NFT on the home chain.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        # Check that this is a wrapper collection
        if not self.storage.get(f"is_wrapper_{local_collection}", False):
            raise ContractError.COLLECTION_NOT_REGISTERED

        foreign_collection = self.storage.get(f"local_to_foreign_{local_collection}")
        if foreign_collection is None:
            raise ContractError.COLLECTION_NOT_REGISTERED

        # Burn wrapper NFT from caller address
        self.env.call(local_collection, "burn", caller, token_id)

        nonce = self.storage.get("lock_nonce", U64(1))
        self.storage.set("lock_nonce", nonce + U64(1))

        self.env.emit_event("nft_wrapper_burned", {
            "local_collection": local_collection,
            "token_id": token_id,
            "burner": caller,
            "foreign_collection": foreign_collection,
            "foreign_recipient": foreign_recipient,
            "nonce": nonce
        })

    @external
    def mint_wrapper_nft(
        self,
        recipient: Address,
        local_collection: Address,
        token_id: U128,
        metadata_uri: Bytes,
        source_tx_hash: Bytes,
        signatures: Vec
    ):
        """
        Mint a new local wrapper NFT representing a bridged foreign NFT.
        Authorized by validator signature threshold.
        """
        self._require_initialized()
        self._require_not_paused()

        if self.storage.get(f"processed_tx_{source_tx_hash}", False):
            raise ContractError.REPLAYED_TX

        # Ensure collection is registered as wrapper
        if not self.storage.get(f"is_wrapper_{local_collection}", False):
            raise ContractError.COLLECTION_NOT_REGISTERED

        # Re-construct signature message: hash(recipient + local_collection + token_id + metadata_uri + source_tx_hash)
        message = self._construct_mint_message(recipient, local_collection, token_id, metadata_uri, source_tx_hash)

        # Verify signatures
        self._verify_signatures(message, signatures)

        # Mark processed
        self.storage.set(f"processed_tx_{source_tx_hash}", True)

        # Call wrapper NFT contract mint
        self.env.call(local_collection, "mint", recipient, token_id, metadata_uri)

        self.env.emit_event("nft_wrapper_minted", {
            "local_collection": local_collection,
            "token_id": token_id,
            "recipient": recipient,
            "metadata_uri": metadata_uri,
            "source_tx_hash": source_tx_hash
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause NFT bridge (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_collection_map(self, local_collection: Address) -> Map:
        """Fetch mapped foreign collection details for local collection."""
        res = Map(self.env)
        foreign = self.storage.get(f"local_to_foreign_{local_collection}")
        if foreign is not None:
            res.set("foreign_collection", foreign)
            res.set("is_wrapper", self.storage.get(f"is_wrapper_{local_collection}", False))
        return res

    @view
    def is_tx_processed(self, source_tx_hash: Bytes) -> Bool:
        """Check if a transfer transaction has been executed."""
        return self.storage.get(f"processed_tx_{source_tx_hash}", False)

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

    def _construct_message(self, recipient: Address, local_col: Address, token_id: U128, tx_hash: Bytes) -> Bytes:
        payload = Bytes(self.env)
        payload.concat(tx_hash)
        payload.concat(Bytes(self.env, str(recipient).encode("utf-8")))
        payload.concat(Bytes(self.env, str(local_col).encode("utf-8")))
        payload.concat(Bytes(self.env, str(token_id).encode("utf-8")))
        return payload

    def _construct_mint_message(
        self,
        recipient: Address,
        local_col: Address,
        token_id: U128,
        meta_uri: Bytes,
        tx_hash: Bytes
    ) -> Bytes:
        payload = Bytes(self.env)
        payload.concat(tx_hash)
        payload.concat(Bytes(self.env, str(recipient).encode("utf-8")))
        payload.concat(Bytes(self.env, str(local_col).encode("utf-8")))
        payload.concat(Bytes(self.env, str(token_id).encode("utf-8")))
        payload.concat(meta_uri)
        return payload

    def _verify_signatures(self, message: Bytes, signatures: Vec):
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
