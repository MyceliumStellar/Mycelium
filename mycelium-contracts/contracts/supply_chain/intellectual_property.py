"""
Intellectual Property — IP registrations, license permissions, royalty tracking, and ownership dispute logs.

Mycelium Smart Contract for Stellar. Manages intellectual property registrations by hashing proof of work/assets.
Enables purchasing licenses (standard commercial/personal) with stablecoin payments, tracks royalty fees
accrued on commercial usage, logs IP ownership disputes, and allows arbitration to resolve ownership or revoke claims.
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
    INVALID_PARAM = 5
    IP_NOT_FOUND = 6
    IP_REVOKED = 7
    LICENSE_EXPIRED = 8
    NO_LICENSE_FOUND = 9
    DISPUTE_ACTIVE = 10
    NO_DISPUTE_ACTIVE = 11

@contract
class IntellectualProperty:
    """
    Intellectual Property registration, licensing, and royalty distribution contract.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        arbitrator: Address,
        stablecoin: Address
    ):
        """Initialize configurations."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("arbitrator", arbitrator)
        self.storage.set("stablecoin", stablecoin)
        self.storage.set("ip_nonce", U64(1))
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "arbitrator": arbitrator,
            "stablecoin": stablecoin
        })

    @external
    def register_ip(
        self,
        caller: Address,
        ip_hash: Bytes,
        ip_name: Symbol,
        license_price: U128,
        royalty_bps: U64
    ) -> U64:
        """Register a new IP asset with license prices and royalty rates."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if royalty_bps > U64(10000) or len(ip_hash) == 0:
            raise ContractError.INVALID_PARAM

        # Check duplicate registration hash
        if self.storage.get(f"hash_to_ip_{ip_hash}") is not None:
            raise ContractError.INVALID_PARAM

        ip_id = self.storage.get("ip_nonce", U64(1))
        self.storage.set("ip_nonce", ip_id + U64(1))

        prefix = f"ip_{ip_id}_"
        self.storage.set(prefix + "owner", caller)
        self.storage.set(prefix + "name", ip_name)
        self.storage.set(prefix + "hash", ip_hash)
        self.storage.set(prefix + "license_price", license_price)
        self.storage.set(prefix + "royalty_bps", royalty_bps)
        self.storage.set(prefix + "total_royalties_paid", U128(0))
        self.storage.set(prefix + "status", Symbol("ACTIVE"))

        # Map hash to id
        self.storage.set(f"hash_to_ip_{ip_hash}", ip_id)

        self.env.emit_event("ip_registered", {
            "ip_id": ip_id,
            "owner": caller,
            "ip_hash": ip_hash,
            "royalty_bps": royalty_bps
        })

        return ip_id

    @external
    def purchase_license(self, caller: Address, ip_id: U64, duration_days: U64):
        """
        Licensees purchase a license for a specific IP.
        Payment is routed directly to the IP owner.
        - duration_days: 0 means lifetime license, else duration in days.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        prefix = f"ip_{ip_id}_"
        status = self.storage.get(prefix + "status")
        if status is None:
            raise ContractError.IP_NOT_FOUND
        if status == Symbol("REVOKED"):
            raise ContractError.IP_REVOKED

        owner = self.storage.get(prefix + "owner")
        price = self.storage.get(prefix + "license_price", U128(0))

        # Transfer license fee to IP owner
        stablecoin = self.storage.get("stablecoin")
        self.env.call(stablecoin, "transfer", caller, owner, price)

        # Set license terms
        lic_prefix = f"lic_{ip_id}_{caller}_"
        self.storage.set(lic_prefix + "active", True)
        
        now = self._get_now()
        if duration_days > U64(0):
            expiry = now + (duration_days * U64(86400))
            self.storage.set(lic_prefix + "expiry", expiry)
        else:
            self.storage.set(lic_prefix + "expiry", U64(0)) # 0 = lifetime

        self.env.emit_event("license_purchased", {
            "ip_id": ip_id,
            "licensee": caller,
            "duration": duration_days,
            "amount_paid": price
        })

    @external
    def pay_royalty(self, caller: Address, ip_id: U64, gross_revenue: U128) -> U128:
        """
        Pay royalty on commercial exploitation of the IP.
        Calculates fee as: gross_revenue * royalty_bps / 10000.
        Transfers royalty fee to the IP owner.
        """
        caller.require_auth()
        self._require_initialized()

        prefix = f"ip_{ip_id}_"
        status = self.storage.get(prefix + "status")
        if status is None:
            raise ContractError.IP_NOT_FOUND
        if status == Symbol("REVOKED"):
            raise ContractError.IP_REVOKED

        # Verify caller has a valid license (not strictly required, anyone can pay royalties on behalf of exposure, but good guard)
        lic_prefix = f"lic_{ip_id}_{caller}_"
        if not self.storage.get(lic_prefix + "active", False):
            raise ContractError.NO_LICENSE_FOUND

        expiry = self.storage.get(lic_prefix + "expiry", U64(0))
        if expiry > U64(0) and self._get_now() > expiry:
            raise ContractError.LICENSE_EXPIRED

        royalty_bps = self.storage.get(prefix + "royalty_bps", U64(0))
        royalty_fee = (gross_revenue * U128(royalty_bps)) / U128(10000)

        if royalty_fee > U128(0):
            owner = self.storage.get(prefix + "owner")
            stablecoin = self.storage.get("stablecoin")
            self.env.call(stablecoin, "transfer", caller, owner, royalty_fee)
            
            # Accumulate historical payout
            paid = self.storage.get(prefix + "total_royalties_paid", U128(0))
            self.storage.set(prefix + "total_royalties_paid", paid + royalty_fee)

        self.env.emit_event("royalty_paid", {
            "ip_id": ip_id,
            "licensee": caller,
            "gross_revenue": gross_revenue,
            "royalty_fee": royalty_fee
        })

        return royalty_fee

    @external
    def raise_ip_dispute(self, caller: Address, ip_id: U64, dispute_evidence_hash: Bytes):
        """Raise an IP dispute (e.g. claim of original creation by claimant)."""
        caller.require_auth()
        self._require_initialized()

        prefix = f"ip_{ip_id}_"
        status = self.storage.get(prefix + "status")
        if status is None:
            raise ContractError.IP_NOT_FOUND
        if status == Symbol("DISPUTED"):
            raise ContractError.DISPUTE_ACTIVE

        self.storage.set(prefix + "status", Symbol("DISPUTED"))
        self.storage.set(prefix + "claimant", caller)
        self.storage.set(prefix + "dispute_evidence", dispute_evidence_hash)

        self.env.emit_event("dispute_raised", {
            "ip_id": ip_id,
            "claimant": caller,
            "evidence_hash": dispute_evidence_hash
        })

    @external
    def resolve_ip_dispute(
        self,
        caller: Address,
        ip_id: U64,
        new_owner: Address,
        revoke_ip: Bool
    ):
        """Arbitrator resolves the dispute. Can re-assign owner or completely revoke the IP."""
        caller.require_auth()
        self._require_initialized()

        arbitrator = self.storage.get("arbitrator")
        if caller != arbitrator:
            raise ContractError.UNAUTHORIZED

        prefix = f"ip_{ip_id}_"
        status = self.storage.get(prefix + "status")
        if status != Symbol("DISPUTED"):
            raise ContractError.NO_DISPUTE_ACTIVE

        if revoke_ip:
            self.storage.set(prefix + "status", Symbol("REVOKED"))
        else:
            self.storage.set(prefix + "status", Symbol("ACTIVE"))
            if new_owner != Address(self.env.current_contract_address()):
                self.storage.set(prefix + "owner", new_owner)

        self.storage.remove(prefix + "claimant")
        self.storage.remove(prefix + "dispute_evidence")

        self.env.emit_event("dispute_resolved", {
            "ip_id": ip_id,
            "revoked": revoke_ip,
            "new_owner": new_owner
        })

    @external
    def revoke_license(self, caller: Address, ip_id: U64, licensee: Address):
        """IP owner can revoke a license if licensee violates terms."""
        caller.require_auth()
        self._require_initialized()

        prefix = f"ip_{ip_id}_"
        owner = self.storage.get(prefix + "owner")
        if caller != owner:
            raise ContractError.UNAUTHORIZED

        lic_prefix = f"lic_{ip_id}_{licensee}_"
        if not self.storage.get(lic_prefix + "active", False):
            raise ContractError.NO_LICENSE_FOUND

        self.storage.set(lic_prefix + "active", False)

        self.env.emit_event("license_revoked", {
            "ip_id": ip_id,
            "licensee": licensee
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause contract operations (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_ip_details(self, ip_id: U64) -> Map:
        """Query IP registration details."""
        res = Map(self.env)
        prefix = f"ip_{ip_id}_"
        owner = self.storage.get(prefix + "owner")
        if owner is not None:
            res.set("owner", owner)
            res.set("name", self.storage.get(prefix + "name"))
            res.set("hash", self.storage.get(prefix + "hash"))
            res.set("license_price", self.storage.get(prefix + "license_price"))
            res.set("royalty_bps", self.storage.get(prefix + "royalty_bps"))
            res.set("total_royalties", self.storage.get(prefix + "total_royalties_paid"))
            res.set("status", self.storage.get(prefix + "status"))
        return res

    @view
    def check_license(self, ip_id: U64, licensee: Address) -> Map:
        """Query license validation state for an account."""
        res = Map(self.env)
        lic_prefix = f"lic_{ip_id}_{licensee}_"
        active = self.storage.get(lic_prefix + "active", False)
        res.set("active", active)
        if active:
            expiry = self.storage.get(lic_prefix + "expiry", U64(0))
            res.set("expiry", expiry)
            res.set("is_expired", (expiry > U64(0) and self._get_now() > expiry))
        else:
            res.set("is_expired", True)
        return res

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
