"""
KYC Registry — Sanctions registry, KYC providers, and compliance level tracking.

Mycelium Smart Contract for Stellar. Tracks on-chain compliance status:
manages a global sanctions list, registers verified KYC providers and compliance officers,
records user KYC levels (Levels 1-3) with expiration timelines, and exposes check points
for external protocol integration.
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
    ADDRESS_SANCTIONED = 5
    PROVIDER_NOT_FOUND = 6
    INVALID_LEVEL = 7
    EXPIRED_KYC = 8
    INVALID_DURATION = 9

@contract
class KYCRegistry:
    """
    KYC and AML compliance registry contract.
    Provides verified compliance state lookup for DeFi, gaming, and tokenized asset platforms.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """Initialize the KYC registry contract."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        # Admin is default compliance officer
        self.storage.set(f"compliance_officer_{admin}", True)

        self.env.emit_event("initialized", {"admin": admin})

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause registry status updates."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- ACCESS CONTROL (ROLES) ---

    @external
    def set_compliance_officer(self, caller: Address, officer: Address, status: Bool):
        """Add or remove a compliance officer. Admin only."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        self.storage.set(f"compliance_officer_{officer}", status)
        self.env.emit_event("compliance_officer_updated", {"officer": officer, "status": status})

    @external
    def set_kyc_provider(self, caller: Address, provider: Address, status: Bool):
        """Register or unregister an authorized third-party KYC Oracle/Provider."""
        caller.require_auth()
        self._require_initialized()
        self._require_compliance_officer(caller)

        self.storage.set(f"kyc_provider_{provider}", status)
        self.env.emit_event("kyc_provider_updated", {"provider": provider, "status": status})

    # --- COMPLIANCE OPERATIONS (SANCTIONS & KYC) ---

    @external
    def set_sanction_status(self, caller: Address, target: Address, sanctioned: Bool):
        """
        Flag or clear an address on the global sanctions list.
        Compliance officer only. Instantly invalidates any KYC compliance status.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_compliance_officer(caller)

        self.storage.set(f"sanctioned_{target}", sanctioned)
        self.env.emit_event("sanction_status_updated", {"target": target, "sanctioned": sanctioned})

    @external
    def update_kyc_status(
        self,
        caller: Address,
        target: Address,
        level: U64,
        duration_sec: U64,
        kyc_hash: Bytes
    ):
        """
        Update the KYC tier level of a user. Authorized KYC provider only.
        
        Args:
            caller: Authorized KYC provider address.
            target: Address of the verified user.
            level: Verified tier (1 = Basic, 2 = ID FaceMatch, 3 = Accredited Source of Funds).
            duration_sec: Validity duration of the verification (e.g. 1 year = 31536000).
            kyc_hash: Secure reference hash to audit reports.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        # Verify caller is registered provider
        if not self.storage.get(f"kyc_provider_{caller}", False):
            raise ContractError.PROVIDER_NOT_FOUND

        # Enforce level constraints
        if level < U64(1) or level > U64(3):
            raise ContractError.INVALID_LEVEL
        if duration_sec == U64(0):
            raise ContractError.INVALID_DURATION

        # Sanction check
        if self.storage.get(f"sanctioned_{target}", False):
            raise ContractError.ADDRESS_SANCTIONED

        expiry = self._get_now() + duration_sec

        self.storage.set(f"kyc_level_{target}", level)
        self.storage.set(f"kyc_expiry_{target}", expiry)
        self.storage.set(f"kyc_hash_{target}", kyc_hash)

        self.env.emit_event("kyc_updated", {
            "target": target,
            "provider": caller,
            "level": level,
            "expiry": expiry
        })

    @external
    def revoke_kyc(self, caller: Address, target: Address):
        """Force revoke user's KYC verification status. Compliance officer only."""
        caller.require_auth()
        self._require_initialized()
        self._require_compliance_officer(caller)

        self.storage.remove(f"kyc_level_{target}")
        self.storage.remove(f"kyc_expiry_{target}")
        self.storage.remove(f"kyc_hash_{target}")

        self.env.emit_event("kyc_revoked", {"target": target, "officer": caller})

    # --- INTEGRATION VIEWS ---

    @view
    def is_compliant(self, target: Address, required_level: U64) -> Bool:
        """
        Check if an address is compliant.
        Returns true if:
          1. Address is not sanctioned
          2. KYC level is greater than or equal to required_level
          3. KYC verification has not expired
        """
        self._require_initialized()

        # Sanction check
        if self.storage.get(f"sanctioned_{target}", False):
            return False

        # Level check
        level = self.storage.get(f"kyc_level_{target}", U64(0))
        if level < required_level:
            return False

        # Expiration check
        expiry = self.storage.get(f"kyc_expiry_{target}", U64(0))
        if self._get_now() > expiry:
            return False

        return True

    @view
    def get_kyc_details(self, target: Address) -> Map:
        """Query detailed KYC profile parameters."""
        res = Map(self.env)
        sanctioned = self.storage.get(f"sanctioned_{target}", False)
        res.set("sanctioned", sanctioned)
        
        level = self.storage.get(f"kyc_level_{target}")
        if level is not None:
            res.set("level", level)
            res.set("expiry", self.storage.get(f"kyc_expiry_{target}"))
            res.set("hash", self.storage.get(f"kyc_hash_{target}"))
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

    def _require_compliance_officer(self, caller: Address):
        if not self.storage.get(f"compliance_officer_{caller}", False):
            raise ContractError.UNAUTHORIZED

    def _get_now(self) -> U64:
        return self.env.ledger_timestamp()
