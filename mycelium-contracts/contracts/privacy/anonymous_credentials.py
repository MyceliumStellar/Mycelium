"""
Anonymous Credentials System — Issuer whitelists, credential presentations, and nullifier double-usage prevention.

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
    ISSUER_NOT_WHITELISTED = 4
    NULLIFIER_ALREADY_USED = 5
    INVALID_SIGNATURE = 6
    EXPIRED_PRESENTATION = 7


@contract
class AnonymousCredentialsSystem:
    """Manages whitelisted cryptographic issuers, logs nullifier usage, and validates zero-knowledge-like credentials."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """Initialize the Anonymous Credentials System."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin})

    # ------------------------------------------------------------------ #
    #  Admin Operations                                                   #
    # ------------------------------------------------------------------ #

    @external
    def set_issuer_status(self, admin: Address, issuer_pubkey: Bytes, status: Bool):
        """Register or remove a trusted identity credential issuer key. Only Admin."""
        self._require_admin(admin)

        self.storage.set(("issuer", issuer_pubkey), status)
        self.env.emit_event("issuer_status_updated", {
            "issuer_pubkey": issuer_pubkey,
            "status": status
        })

    # ------------------------------------------------------------------ #
    #  Credential Operations                                              #
    # ------------------------------------------------------------------ #

    @external
    def present_credential(
        self,
        caller: Address,
        credential_hash: Bytes,
        nullifier: Bytes,
        issuer_pubkey: Bytes,
        signature: Bytes,
        expiration: U64
    ) -> Bool:
        """Present a cryptographic credential signed by a whitelisted issuer. Records nullifier to prevent reuse."""
        self._require_initialized()
        caller.require_auth()

        # Check if issuer is whitelisted
        if not self.storage.get(("issuer", issuer_pubkey), False):
            raise ContractError.ISSUER_NOT_WHITELISTED

        # Prevent duplicate presentation using nullifier checks
        if self.storage.get(("nullifier", nullifier), False):
            raise ContractError.NULLIFIER_ALREADY_USED

        # Check presentation timeframe
        now = self.env.ledger().timestamp()
        if now > expiration:
            raise ContractError.EXPIRED_PRESENTATION

        # Cryptographic verification:
        # The issuer signs the payload: keccak256(caller + credential_hash + nullifier + expiration)
        # This prevents credential transferability (front-running/stealing)
        message = self.env.crypto().keccak256(caller, credential_hash, nullifier, expiration)

        # Verify signature using Ed25519
        valid = self.env.crypto().verify_sig_ed25519(issuer_pubkey, message, signature)
        if not valid:
            raise ContractError.INVALID_SIGNATURE

        # Register nullifier as consumed
        self.storage.set(("nullifier", nullifier), True)
        
        # Save verification state for the caller and credential type
        self.storage.set(("verified", caller, credential_hash), True)

        self.env.emit_event("credential_presented", {
            "caller": caller,
            "credential_hash": credential_hash,
            "nullifier": nullifier
        })

        return True

    # ------------------------------------------------------------------ #
    #  View Operations                                                    #
    # ------------------------------------------------------------------ #

    @view
    def is_user_verified(self, user: Address, credential_hash: Bytes) -> Bool:
        """Check if user has successfully presented a specific credential type."""
        self._require_initialized()
        return self.storage.get(("verified", user, credential_hash), False)

    @view
    def is_nullifier_consumed(self, nullifier: Bytes) -> Bool:
        """Check if a credential nullifier has already been recorded."""
        self._require_initialized()
        return self.storage.get(("nullifier", nullifier), False)

    @view
    def is_issuer_valid(self, issuer_pubkey: Bytes) -> Bool:
        """Check if an issuer public key is in the whitelist."""
        self._require_initialized()
        return self.storage.get(("issuer", issuer_pubkey), False)

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                   #
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
