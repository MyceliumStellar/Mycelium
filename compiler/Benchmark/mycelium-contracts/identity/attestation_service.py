"""
Attestation Service — EAS-style schema registry and on-chain attestations.

Mycelium Smart Contract for Stellar. Registers EAS-style schemas,
generates unique cryptographic UID hashes for attestations, routes custom hooks
to external resolver contracts, and tracks attestation revocation records.
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
    SCHEMA_NOT_FOUND = 5
    ATTESTATION_NOT_FOUND = 6
    NOT_REVOCABLE = 7
    ALREADY_REVOKED = 8
    EXPIRED = 9
    RESOLVER_REJECTED = 10

@contract
class AttestationService:
    """
    Ethereum Attestation Service (EAS) model for Stellar.
    Allows individuals and protocols to attest facts, verify claims,
    and hook validation rules through external resolver contracts.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """Initialize the attestation contract."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("next_schema_id", U64(1))
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin})

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause attestation actions."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- EAS SCHEMA REGISTRY ---

    @external
    def register_schema(self, caller: Address, definition: Bytes, resolver: Address) -> U64:
        """
        Register a new schema definition.
        
        Args:
            caller: Creator of the schema.
            definition: String or hash format describing variables.
            resolver: Optional contract address to hook validation callbacks.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        schema_id = self.storage.get("next_schema_id", U64(1))
        
        self.storage.set(f"schema_active_{schema_id}", True)
        self.storage.set(f"schema_def_{schema_id}", definition)
        self.storage.set(f"schema_resolver_{schema_id}", resolver)
        
        self.storage.set("next_schema_id", schema_id + U64(1))

        self.env.emit_event("schema_registered", {
            "schema_id": schema_id,
            "creator": caller,
            "resolver": resolver,
            "definition": definition
        })

        return schema_id

    # --- ATTESTATION CREATION & REVOCATION ---

    @external
    def attest(
        self,
        caller: Address,
        schema_id: U64,
        recipient: Address,
        expiration_time: U64,
        revocable: Bool,
        data: Bytes
    ) -> Bytes:
        """
        Create an attestation. Computes a unique UID and executes resolver callback hooks.
        
        Args:
            caller: Attester address.
            schema_id: Schema format identifier.
            recipient: Attestation subject recipient address.
            expiration_time: Optional expiration timestamp (0 if never expires).
            revocable: Flag indicating if this attestation is revocable.
            data: Arbitrary binary attestation parameters.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        # Schema existence check
        if not self.storage.get(f"schema_active_{schema_id}", False):
            raise ContractError.SCHEMA_NOT_FOUND

        # Generate unique UID
        nonce = self.storage.get(f"nonce_{caller}", U64(0))
        self.storage.set(f"nonce_{caller}", nonce + U64(1))

        # Hash: sha256 of schema_id + attester + recipient + nonce
        uid_input = Bytes(f"{schema_id}_{caller}_{recipient}_{nonce}".encode("utf-8"))
        uid = self.env.crypto().sha256(uid_input)

        # Trigger resolver callback if configured
        resolver = self.storage.get(f"schema_resolver_{schema_id}")
        if resolver is not None and resolver != Address(self.env):
            # Try to query callback. Must return true to confirm.
            try:
                success = self.env.call(resolver, "on_attest", caller, recipient, schema_id, uid, data)
                if not success:
                    raise ContractError.RESOLVER_REJECTED
            except Exception:
                raise ContractError.RESOLVER_REJECTED

        # Record attestation state
        self.storage.set(f"att_active_{uid}", True)
        self.storage.set(f"att_schema_{uid}", schema_id)
        self.storage.set(f"att_attester_{uid}", caller)
        self.storage.set(f"att_recipient_{uid}", recipient)
        self.storage.set(f"att_expiry_{uid}", expiration_time)
        self.storage.set(f"att_revocable_{uid}", revocable)
        self.storage.set(f"att_revoked_{uid}", False)
        self.storage.set(f"att_data_{uid}", data)

        self.env.emit_event("attested", {
            "uid": uid,
            "schema_id": schema_id,
            "attester": caller,
            "recipient": recipient
        })

        return uid

    @external
    def revoke(self, caller: Address, uid: Bytes):
        """
        Revoke an active attestation. Only the original attester can revoke.
        """
        caller.require_auth()
        self._require_initialized()

        if not self.storage.get(f"att_active_{uid}", False):
            raise ContractError.ATTESTATION_NOT_FOUND

        if self.storage.get(f"att_revoked_{uid}", False):
            raise ContractError.ALREADY_REVOKED

        if not self.storage.get(f"att_revocable_{uid}", False):
            raise ContractError.NOT_REVOCABLE

        attester = self.storage.get(f"att_attester_{uid}")
        if caller != attester:
            raise ContractError.UNAUTHORIZED

        schema_id = self.storage.get(f"att_schema_{uid}", U64(0))
        recipient = self.storage.get(f"att_recipient_{uid}")

        # Trigger resolver callback if configured
        resolver = self.storage.get(f"schema_resolver_{schema_id}")
        if resolver is not None and resolver != Address(self.env):
            try:
                success = self.env.call(resolver, "on_revoke", caller, recipient, schema_id, uid)
                if not success:
                    raise ContractError.RESOLVER_REJECTED
            except Exception:
                raise ContractError.RESOLVER_REJECTED

        self.storage.set(f"att_revoked_{uid}", True)

        self.env.emit_event("revoked", {
            "uid": uid,
            "schema_id": schema_id,
            "attester": caller
        })

    # --- VIEWS ---

    @view
    def is_valid_attestation(self, uid: Bytes) -> Bool:
        """
        Checks if an attestation is valid (exists, not revoked, and not expired).
        """
        self._require_initialized()
        
        if not self.storage.get(f"att_active_{uid}", False):
            return False

        if self.storage.get(f"att_revoked_{uid}", False):
            return False

        expiry = self.storage.get(f"att_expiry_{uid}", U64(0))
        if expiry > U64(0) and self._get_now() > expiry:
            return False

        return True

    @view
    def get_attestation(self, uid: Bytes) -> Map:
        """Inspect all metadata parameters of an attestation."""
        self._require_initialized()
        if not self.storage.get(f"att_active_{uid}", False):
            raise ContractError.ATTESTATION_NOT_FOUND

        res = Map(self.env)
        res.set("schema_id", self.storage.get(f"att_schema_{uid}"))
        res.set("attester", self.storage.get(f"att_attester_{uid}"))
        res.set("recipient", self.storage.get(f"att_recipient_{uid}"))
        res.set("expiry", self.storage.get(f"att_expiry_{uid}"))
        res.set("revocable", self.storage.get(f"att_revocable_{uid}"))
        res.set("revoked", self.storage.get(f"att_revoked_{uid}"))
        res.set("data", self.storage.get(f"att_data_{uid}"))
        return res

    @view
    def get_schema(self, schema_id: U64) -> Map:
        """Inspect schema details."""
        res = Map(self.env)
        if self.storage.get(f"schema_active_{schema_id}", False):
            res.set("definition", self.storage.get(f"schema_def_{schema_id}"))
            res.set("resolver", self.storage.get(f"schema_resolver_{schema_id}"))
        return res

    # --- INTERNAL HELPERS ---

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_not_paused(self):
        if self.storage.get("paused", False):
            raise ContractError.PAUSED

    def _require_admin(self, caller: Address):
        if caller != self.storage.get("admin"):
            raise ContractError.UNAUTHORIZED

    def _get_now(self) -> U64:
        return self.env.ledger_timestamp()
