"""
Document Notarization — Document hash anchoring, timestamps, creator keys, multi-sig co-signing, and revocation logs.

Mycelium Smart Contract for Stellar. Enables anchoring document hashes as cryptographic proofs of existence and integrity.
Supports optional multi-signature co-signing thresholds, document revocation logs, metadata updates, and validation queries
to verify document status, creator keys, and block timestamps.
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
    INVALID_THRESHOLD = 5
    DOCUMENT_NOT_FOUND = 6
    DUPLICATE_DOCUMENT = 7
    DOCUMENT_REVOKED = 8
    ALREADY_APPROVED = 9
    NOT_A_COSIGNER = 10

@contract
class DocumentNotarization:
    """
    Document Notarization Contract supporting multi-sig approvals and revocation.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """Initialize contract admin controls."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("doc_nonce", U64(1))
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin
        })

    @external
    def notarize_document(
        self,
        caller: Address,
        doc_hash: Bytes,
        doc_name: Symbol,
        metadata: Bytes,
        co_signers: Vec,    # Vec of Address (optional co-signers)
        threshold: U64      # Approval threshold required (0 if single-sig)
    ) -> U64:
        """
        Notarize a document hash.
        If co_signers is empty, the document is immediately verified.
        Otherwise, it stays pending until threshold approvals are met.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        # Check for duplicate hashes
        if self.storage.get(f"hash_to_id_{doc_hash}") is not None:
            raise ContractError.DUPLICATE_DOCUMENT

        num_cosigners = len(co_signers)
        if num_cosigners > 0:
            if threshold == U64(0) or threshold > num_cosigners:
                raise ContractError.INVALID_THRESHOLD
        else:
            if threshold > U64(0):
                raise ContractError.INVALID_THRESHOLD

        doc_id = self.storage.get("doc_nonce", U64(1))
        self.storage.set("doc_nonce", doc_id + U64(1))

        prefix = f"doc_{doc_id}_"
        self.storage.set(prefix + "hash", doc_hash)
        self.storage.set(prefix + "name", doc_name)
        self.storage.set(prefix + "metadata", metadata)
        self.storage.set(prefix + "creator", caller)
        self.storage.set(prefix + "timestamp", self._get_now())
        self.storage.set(prefix + "block_number", self.env.ledger_sequence())
        
        # Co-signers state setup
        self.storage.set(prefix + "threshold", threshold)
        self.storage.set(prefix + "cosigners_count", num_cosigners)
        self.storage.set(prefix + "approvals_count", U64(0))
        self.storage.set(prefix + "status", Symbol("PENDING") if num_cosigners > 0 else Symbol("VERIFIED"))

        for i in range(num_cosigners):
            signer = co_signers.get(i)
            self.storage.set(prefix + f"cosigner_{signer}", True)

        # Map hash to id
        self.storage.set(f"hash_to_id_{doc_hash}", doc_id)

        self.env.emit_event("document_notarized", {
            "doc_id": doc_id,
            "doc_hash": doc_hash,
            "creator": caller,
            "is_pending": num_cosigners > 0
        })

        return doc_id

    @external
    def approve_document(self, caller: Address, doc_id: U64):
        """Co-signer approves the pending document."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        prefix = f"doc_{doc_id}_"
        status = self.storage.get(prefix + "status")
        if status is None:
            raise ContractError.DOCUMENT_NOT_FOUND
        if status != Symbol("PENDING"):
            raise ContractError.INVALID_THRESHOLD

        if not self.storage.get(prefix + f"cosigner_{caller}", False):
            raise ContractError.NOT_A_COSIGNER

        if self.storage.get(prefix + f"approved_{caller}", False):
            raise ContractError.ALREADY_APPROVED

        # Record approval
        self.storage.set(prefix + f"approved_{caller}", True)
        approvals = self.storage.get(prefix + "approvals_count", U64(0)) + U64(1)
        self.storage.set(prefix + "approvals_count", approvals)

        # Check threshold
        threshold = self.storage.get(prefix + "threshold", U64(0))
        if approvals >= threshold:
            self.storage.set(prefix + "status", Symbol("VERIFIED"))
            self.env.emit_event("document_verified", {
                "doc_id": doc_id,
                "approvals": approvals
            })
        else:
            self.env.emit_event("document_approved", {
                "doc_id": doc_id,
                "approver": caller,
                "current_approvals": approvals
            })

    @external
    def update_metadata(self, caller: Address, doc_id: U64, new_metadata: Bytes):
        """Update metadata of a notarized document (Creator only)."""
        caller.require_auth()
        self._require_initialized()

        prefix = f"doc_{doc_id}_"
        creator = self.storage.get(prefix + "creator")
        if creator is None:
            raise ContractError.DOCUMENT_NOT_FOUND

        if caller != creator:
            raise ContractError.UNAUTHORIZED

        status = self.storage.get(prefix + "status")
        if status == Symbol("REVOKED"):
            raise ContractError.DOCUMENT_REVOKED

        self.storage.set(prefix + "metadata", new_metadata)

        self.env.emit_event("metadata_updated", {
            "doc_id": doc_id,
            "updater": caller
        })

    @external
    def revoke_document(self, caller: Address, doc_id: U64, reason: Symbol):
        """Revoke a document notarization (Creator or Admin only)."""
        caller.require_auth()
        self._require_initialized()

        prefix = f"doc_{doc_id}_"
        creator = self.storage.get(prefix + "creator")
        if creator is None:
            raise ContractError.DOCUMENT_NOT_FOUND

        admin = self.storage.get("admin")
        if caller != creator and caller != admin:
            raise ContractError.UNAUTHORIZED

        status = self.storage.get(prefix + "status")
        if status == Symbol("REVOKED"):
            raise ContractError.DOCUMENT_REVOKED

        self.storage.set(prefix + "status", Symbol("REVOKED"))
        self.storage.set(prefix + "revocation_reason", reason)
        self.storage.set(prefix + "revocation_time", self._get_now())

        self.env.emit_event("document_revoked", {
            "doc_id": doc_id,
            "revoker": caller,
            "reason": reason
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause document creations (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_document(self, doc_id: U64) -> Map:
        """Query document details by ID."""
        res = Map(self.env)
        prefix = f"doc_{doc_id}_"
        doc_hash = self.storage.get(prefix + "hash")
        if doc_hash is not None:
            res.set("doc_id", doc_id)
            res.set("hash", doc_hash)
            res.set("name", self.storage.get(prefix + "name"))
            res.set("metadata", self.storage.get(prefix + "metadata"))
            res.set("creator", self.storage.get(prefix + "creator"))
            res.set("timestamp", self.storage.get(prefix + "timestamp"))
            res.set("block", self.storage.get(prefix + "block_number"))
            res.set("status", self.storage.get(prefix + "status"))
            res.set("approvals", self.storage.get(prefix + "approvals_count"))
            res.set("threshold", self.storage.get(prefix + "threshold"))
            if self.storage.get(prefix + "status") == Symbol("REVOKED"):
                res.set("revocation_reason", self.storage.get(prefix + "revocation_reason"))
                res.set("revocation_time", self.storage.get(prefix + "revocation_time"))
        return res

    @view
    def get_document_by_hash(self, doc_hash: Bytes) -> Map:
        """Verify if a document hash is notarized and retrieve its parameters."""
        doc_id = self.storage.get(f"hash_to_id_{doc_hash}")
        if doc_id is None:
            res = Map(self.env)
            res.set("exists", False)
            return res
        
        details = self.get_document(doc_id)
        details.set("exists", True)
        return details

    @view
    def is_approved_by(self, doc_id: U64, account: Address) -> Bool:
        """Check if a co-signer has approved the document."""
        prefix = f"doc_{doc_id}_"
        return self.storage.get(prefix + f"approved_{account}", False)

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

    def _get_now(self) -> U64:
        return self.env.ledger_timestamp()
