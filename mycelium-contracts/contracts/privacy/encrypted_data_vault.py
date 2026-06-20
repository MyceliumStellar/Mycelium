"""
Encrypted Data Vault — Access keys registry, encrypted payload storage, decay timestamps, and access request checks.

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
    TRANSFER_FAILED = 4
    VAULT_NOT_FOUND = 5
    VAULT_EXPIRED = 6
    ACCESS_DENIED = 7
    INVALID_DECAY_TIMESTAMP = 8
    ALREADY_ACTIVE = 9
    ACCESS_EXPIRED = 10


@contract
class EncryptedDataVault:
    """Manages secure storage references of encrypted payloads, decay deadlines, and dynamic access authorizations."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, token: Address):
        """Initialize the Encrypted Data Vault contract."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("token", token)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin, "token": token})

    # ------------------------------------------------------------------ #
    #  Owner Operations                                                  #
    # ------------------------------------------------------------------ #

    @external
    def store_payload(
        self,
        owner: Address,
        payload_id: Symbol,
        payload_uri: Symbol,
        decay_duration: U64,
        access_fee: U128
    ) -> Bool:
        """Upload/register a reference to an encrypted payload. Sets the decay expiration time."""
        self._require_initialized()
        owner.require_auth()

        if decay_duration == U64(0):
            raise ContractError.INVALID_DECAY_TIMESTAMP

        now = self.env.ledger().timestamp()
        decay_timestamp = now + decay_duration

        vault = {
            "owner": owner,
            "uri": payload_uri,
            "decay_timestamp": decay_timestamp,
            "access_fee": access_fee,
            "active": True
        }

        self.storage.set(("vault", owner, payload_id), vault)
        self.env.emit_event("payload_stored", {
            "owner": owner,
            "payload_id": payload_id,
            "decay_timestamp": decay_timestamp,
            "access_fee": access_fee
        })

        return True

    @external
    def grant_access(
        self,
        owner: Address,
        payload_id: Symbol,
        viewer: Address,
        duration: U64
    ):
        """Manually grant a viewer access to a specific payload for a set duration. Only Owner."""
        self._require_initialized()
        owner.require_auth()

        vault = self.storage.get(("vault", owner, payload_id), None)
        if vault is None:
            raise ContractError.VAULT_NOT_FOUND

        now = self.env.ledger().timestamp()
        if now > vault["decay_timestamp"]:
            raise ContractError.VAULT_EXPIRED

        access_expiry = now + duration
        self.storage.set(("access", owner, payload_id, viewer), access_expiry)

        self.env.emit_event("access_granted", {
            "owner": owner,
            "payload_id": payload_id,
            "viewer": viewer,
            "expiry": access_expiry
        })

    @external
    def revoke_access(self, owner: Address, payload_id: Symbol, viewer: Address):
        """Revoke a viewer's access. Only Owner."""
        self._require_initialized()
        owner.require_auth()

        self.storage.set(("access", owner, payload_id, viewer), U64(0))
        self.env.emit_event("access_revoked", {"owner": owner, "payload_id": payload_id, "viewer": viewer})

    @external
    def register_access_key(self, owner: Address, pubkey: Bytes, valid: Bool):
        """Register or revoke an authorized viewer public key for owner vault. Only Owner."""
        self._require_initialized()
        owner.require_auth()

        self.storage.set(("owner_key", owner, pubkey), valid)
        self.env.emit_event("access_key_updated", {"owner": owner, "pubkey": pubkey, "valid": valid})

    # ------------------------------------------------------------------ #
    #  Viewer Operations                                                 #
    # ------------------------------------------------------------------ #

    @external
    def request_access_with_payment(
        self,
        viewer: Address,
        owner: Address,
        payload_id: Symbol,
        duration: U64
    ) -> Bool:
        """Acquire payload access by paying the access fee. Pays tokens directly to payload owner."""
        self._require_initialized()
        viewer.require_auth()

        vault = self.storage.get(("vault", owner, payload_id), None)
        if vault is None:
            raise ContractError.VAULT_NOT_FOUND

        now = self.env.ledger().timestamp()
        if now > vault["decay_timestamp"] or not vault["active"]:
            raise ContractError.VAULT_EXPIRED

        # Process payment
        fee = vault["access_fee"]
        if fee > U128(0):
            token = self.storage.get("token")
            # Transfer fee from viewer directly to vault owner
            success = self.env.invoke_contract(token, "transfer", [viewer, owner, fee])
            if not success:
                raise ContractError.TRANSFER_FAILED

        # Record access permission
        access_expiry = now + duration
        self.storage.set(("access", owner, payload_id, viewer), access_expiry)

        self.env.emit_event("access_purchased", {
            "viewer": viewer,
            "owner": owner,
            "payload_id": payload_id,
            "expiry": access_expiry
        })

        return True

    # ------------------------------------------------------------------ #
    #  View Operations                                                    #
    # ------------------------------------------------------------------ #

    @view
    def read_payload_reference(
        self,
        viewer: Address,
        owner: Address,
        payload_id: Symbol
    ) -> Symbol:
        """View the URI payload reference. Requires valid access authorization and active decay timeline."""
        self._require_initialized()
        viewer.require_auth()

        vault = self.storage.get(("vault", owner, payload_id), None)
        if vault is None or not vault["active"]:
            raise ContractError.VAULT_NOT_FOUND

        now = self.env.ledger().timestamp()

        # Enforce decay timestamp boundary
        if now > vault["decay_timestamp"]:
            raise ContractError.VAULT_EXPIRED

        # Owner has perpetual access to their own payload prior to decay
        if viewer == owner:
            return vault["uri"]

        # Check access registry
        expiry = self.storage.get(("access", owner, payload_id, viewer), U64(0))
        if now > expiry:
            raise ContractError.ACCESS_EXPIRED

        return vault["uri"]

    @view
    def get_vault_meta(self, owner: Address, payload_id: Symbol) -> Map:
        """Get public metadata of a vault payload."""
        self._require_initialized()
        vault = self.storage.get(("vault", owner, payload_id), None)
        if vault is None:
            raise ContractError.VAULT_NOT_FOUND

        res = Map()
        res.set(Symbol("owner"), vault["owner"])
        res.set(Symbol("decay_timestamp"), vault["decay_timestamp"])
        res.set(Symbol("access_fee"), vault["access_fee"])
        res.set(Symbol("active"), vault["active"])
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                   #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
