"""
Credential Verifier — Verifiable credentials, schema registry, and selective disclosure.

Mycelium Smart Contract for Stellar. Registers issuer schemas, stores verifiable credential
hashes, maintains a revocation registry, and enables selective disclosure verification
of sub-claims on-chain.
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
    SCHEMA_ALREADY_EXISTS = 5
    SCHEMA_NOT_FOUND = 6
    CREDENTIAL_NOT_FOUND = 7
    CREDENTIAL_REVOKED = 8
    LENGTH_MISMATCH = 9
    CLAIM_NOT_FOUND = 10
    CLAIM_MISMATCH = 11

@contract
class CredentialVerifier:
    """
    A smart contract managing verifiable credentials and schemas.
    Allows issuers to attest claims and verify selective components of credentials.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """Initialize the credential verifier contract."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin})

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause attestations and verifications."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- SCHEMA REGISTRY ---

    @external
    def register_schema(self, caller: Address, schema_id: U64, schema_definition_hash: Bytes):
        """
        Register a new credential schema (e.g. Identity, DriverLicense, Degree).
        Only the registered schema issuer can attest credentials under this schema.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if self.storage.get(f"schema_active_{schema_id}", False):
            raise ContractError.SCHEMA_ALREADY_EXISTS

        self.storage.set(f"schema_active_{schema_id}", True)
        self.storage.set(f"schema_issuer_{schema_id}", caller)
        self.storage.set(f"schema_hash_{schema_id}", schema_definition_hash)

        self.env.emit_event("schema_registered", {
            "schema_id": schema_id,
            "issuer": caller,
            "hash": schema_definition_hash
        })

    # --- CREDENTIAL ATTESTATION & REVOCATION ---

    @external
    def attest_credential(
        self,
        caller: Address,
        subject: Address,
        schema_id: U64,
        credential_hash: Bytes,
        claim_keys: Vec,
        claim_hashes: Vec
    ):
        """
        Attest a credential for a subject. Inserts main hash and selective sub-claim hashes.
        
        Args:
            caller: Issuer of the credential, must be the registered schema owner.
            subject: Holder/subject address.
            schema_id: The ID of the registered schema.
            credential_hash: Hash of the full credential document.
            claim_keys: Keys of discloseable fields (e.g., "age_over_21", "nationality").
            claim_hashes: Hashed values of those fields for selective disclosure verification.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        # Check schema and issuer permissions
        if not self.storage.get(f"schema_active_{schema_id}", False):
            raise ContractError.SCHEMA_NOT_FOUND
        if self.storage.get(f"schema_issuer_{schema_id}") != caller:
            raise ContractError.UNAUTHORIZED

        if len(claim_keys) != len(claim_hashes):
            raise ContractError.LENGTH_MISMATCH

        cred_key = f"{caller}_{subject}_{schema_id}"
        self.storage.set(f"cred_active_{cred_key}", True)
        self.storage.set(f"cred_hash_{cred_key}", credential_hash)
        self.storage.set(f"cred_revoked_{cred_key}", False)

        # Store sub-claim hashes for selective disclosure
        self.storage.set(f"claims_count_{cred_key}", len(claim_keys))
        for i in range(len(claim_keys)):
            key = claim_keys.get(i)
            val_hash = claim_hashes.get(i)
            self.storage.set(f"claim_hash_{cred_key}_{key}", val_hash)

        self.env.emit_event("credential_attested", {
            "issuer": caller,
            "subject": subject,
            "schema_id": schema_id,
            "credential_hash": credential_hash
        })

    @external
    def revoke_credential(self, caller: Address, subject: Address, schema_id: U64):
        """
        Revoke an active credential. Only the credential issuer can revoke.
        """
        caller.require_auth()
        self._require_initialized()

        cred_key = f"{caller}_{subject}_{schema_id}"
        if not self.storage.get(f"cred_active_{cred_key}", False):
            raise ContractError.CREDENTIAL_NOT_FOUND

        if self.storage.get(f"cred_revoked_{cred_key}", False):
            raise ContractError.UNAUTHORIZED # Already revoked

        self.storage.set(f"cred_revoked_{cred_key}", True)

        self.env.emit_event("credential_revoked", {
            "issuer": caller,
            "subject": subject,
            "schema_id": schema_id
        })

    # --- VERIFICATION VIEWS ---

    @view
    def verify_credential(self, issuer: Address, subject: Address, schema_id: U64, credential_hash: Bytes) -> Bool:
        """
        Verify if a credential exists, is active, matches the hash, and is not revoked.
        """
        self._require_initialized()

        cred_key = f"{issuer}_{subject}_{schema_id}"
        if not self.storage.get(f"cred_active_{cred_key}", False):
            return False

        if self.storage.get(f"cred_revoked_{cred_key}", False):
            return False

        stored_hash = self.storage.get(f"cred_hash_{cred_key}")
        return stored_hash == credential_hash

    @view
    def verify_selective_disclosure(
        self,
        issuer: Address,
        subject: Address,
        schema_id: U64,
        claim_key: Bytes,
        disclosed_value: Bytes
    ) -> Bool:
        """
        Verify a single disclosed claim without revealing the rest of the credential.
        Hashes the disclosed value and matches it against the stored claim hash.
        """
        self._require_initialized()

        cred_key = f"{issuer}_{subject}_{schema_id}"
        if not self.storage.get(f"cred_active_{cred_key}", False):
            return False
        if self.storage.get(f"cred_revoked_{cred_key}", False):
            return False

        stored_claim_hash = self.storage.get(f"claim_hash_{cred_key}_{claim_key}")
        if stored_claim_hash is None:
            return False

        # Hash disclosed value to compare
        calculated_hash = self.env.crypto().sha256(disclosed_value) # Standard hash function in Mycelium
        return stored_claim_hash == calculated_hash

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
