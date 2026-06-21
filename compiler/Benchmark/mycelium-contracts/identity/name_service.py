"""
Stellar Name Service — Domain registration, subdomain parsing, and metadata.

Mycelium Smart Contract for Stellar. Facilitates domain registration with fee schedules based
on character length, handles grace periods, supports subdomain creation by parent domain owners,
manages text records, and provides forward/reverse address resolution.
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
    INVALID_NAME = 5
    DOMAIN_TAKEN = 6
    DOMAIN_EXPIRED = 7
    PARENT_NOT_OWNED = 8
    INSUFFICIENT_FUNDS = 9
    GRACE_PERIOD_ACTIVE = 10

@contract
class StellarNameService:
    """
    Decentralized name service contract.
    Resolves human-readable domain names to Stellar addresses.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        payment_token: Address,
        price_3_char: U128,
        price_4_char: U128,
        price_default: U128,
        grace_period_sec: U64
    ):
        """Initialize the name service parameters."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("payment_token", payment_token)
        self.storage.set("price_3", price_3_char)
        self.storage.set("price_4", price_4_char)
        self.storage.set("price_def", price_default)
        self.storage.set("grace_period", grace_period_sec)
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin, "payment_token": payment_token})

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause domain registrations."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- DOMAIN OPERATIONS ---

    @external
    def register_domain(self, caller: Address, name: Bytes, duration_years: U64):
        """
        Register a root domain (e.g. "alice").
        
        Args:
            caller: Buyer/registrant address.
            name: Root name string as Bytes (must not contain dots).
            duration_years: Number of years to register (1 year = 31536000 seconds).
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        # Validate name layout (no dots)
        if not self._is_valid_name(name):
            raise ContractError.INVALID_NAME

        name_hash = self.env.crypto().sha256(name)
        now = self._get_now()
        expiry = self.storage.get(f"expiry_{name_hash}", U64(0))
        grace = self.storage.get("grace_period", U64(0))

        # Check availability
        if expiry > U64(0) and now < expiry + grace:
            raise ContractError.DOMAIN_TAKEN

        # Determine price based on character length
        char_len = len(name)
        price_per_year = self.storage.get("price_def", U128(0))
        if char_len <= 3:
            price_per_year = self.storage.get("price_3", U128(0))
        elif char_len == 4:
            price_per_year = self.storage.get("price_4", U128(0))

        total_price = price_per_year * U128(duration_years)

        # Collect fee
        if total_price > U128(0):
            token = self.storage.get("payment_token")
            self.env.call(token, "transfer", caller, self.storage.get("admin"), total_price)

        # Setup ownership and expiry
        reg_duration = duration_years * U64(31536000)
        new_expiry = now + reg_duration

        self.storage.set(f"owner_{name_hash}", caller)
        self.storage.set(f"expiry_{name_hash}", new_expiry)
        self.storage.set(f"name_{name_hash}", name)

        self.env.emit_event("domain_registered", {
            "name": name,
            "owner": caller,
            "expiry": new_expiry,
            "price": total_price
        })

    @external
    def register_subdomain(self, caller: Address, parent_name: Bytes, sub_name: Bytes):
        """
        Register a subdomain (e.g. "blog" under "alice", resulting in "blog.alice").
        Only the parent owner can create subdomains. Free of charge.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if not self._is_valid_name(sub_name):
            raise ContractError.INVALID_NAME

        parent_hash = self.env.crypto().sha256(parent_name)
        self._require_active_owner(parent_hash, caller)

        # Build subdomain name: sub_name + '.' + parent_name
        # Simple bytes formatting
        sub_full = Bytes(sub_name + b"." + parent_name)
        sub_hash = self.env.crypto().sha256(sub_full)

        # Subdomains inherit parent expiry or expire directly with parent
        parent_expiry = self.storage.get(f"expiry_{parent_hash}", U64(0))

        self.storage.set(f"owner_{sub_hash}", caller)
        self.storage.set(f"expiry_{sub_hash}", parent_expiry)
        self.storage.set(f"name_{sub_hash}", sub_full)

        self.env.emit_event("subdomain_registered", {
            "full_name": sub_full,
            "owner": caller,
            "parent": parent_name
        })

    @external
    def renew_domain(self, caller: Address, name: Bytes, duration_years: U64):
        """
        Renew an active or grace-period domain.
        Original owner can renew during grace period. Anyone can renew if active.
        """
        self._require_initialized()

        name_hash = self.env.crypto().sha256(name)
        expiry = self.storage.get(f"expiry_{name_hash}", U64(0))
        if expiry == U64(0):
            raise ContractError.DOMAIN_TAKEN

        now = self._get_now()
        grace = self.storage.get("grace_period", U64(0))

        # Check if fully expired past grace period
        if now >= expiry + grace:
            raise ContractError.DOMAIN_EXPIRED

        owner = self.storage.get(f"owner_{name_hash}")

        # If in grace period, only the original owner can renew
        if now >= expiry and caller != owner:
            raise ContractError.GRACE_PERIOD_ACTIVE

        # Pricing
        char_len = len(name)
        price_per_year = self.storage.get("price_def", U128(0))
        if char_len <= 3:
            price_per_year = self.storage.get("price_3", U128(0))
        elif char_len == 4:
            price_per_year = self.storage.get("price_4", U128(0))

        total_price = price_per_year * U128(duration_years)

        # Collect fee
        if total_price > U128(0):
            caller.require_auth()
            token = self.storage.get("payment_token")
            self.env.call(token, "transfer", caller, self.storage.get("admin"), total_price)

        # Adjust expiry
        start_point = expiry if now < expiry else now
        new_expiry = start_point + (duration_years * U64(31536000))
        self.storage.set(f"expiry_{name_hash}", new_expiry)

        self.env.emit_event("domain_renewed", {
            "name": name,
            "new_expiry": new_expiry,
            "cost": total_price
        })

    # --- METADATA (TEXT RECORDS) & RESOLUTION ---

    @external
    def set_text_record(self, caller: Address, name: Bytes, key: Bytes, value: Bytes):
        """Configure custom metadata text records (e.g. 'avatar', 'email'). Owner only."""
        caller.require_auth()
        self._require_initialized()

        name_hash = self.env.crypto().sha256(name)
        self._require_active_owner(name_hash, caller)

        self.storage.set(f"text_{name_hash}_{key}", value)
        self.env.emit_event("text_record_updated", {
            "name": name,
            "key": key,
            "value": value
        })

    @external
    def set_primary_name(self, caller: Address, name: Bytes):
        """Set primary domain name for reverse resolution. Caller must own the domain."""
        caller.require_auth()
        self._require_initialized()

        name_hash = self.env.crypto().sha256(name)
        self._require_active_owner(name_hash, caller)

        self.storage.set(f"primary_{caller}", name)
        self.env.emit_event("primary_name_updated", {"address": caller, "name": name})

    # --- VIEWS ---

    @view
    def resolve(self, name: Bytes) -> Address:
        """Forward resolution: resolve domain string to address."""
        self._require_initialized()
        name_hash = self.env.crypto().sha256(name)
        
        # Check expiry
        expiry = self.storage.get(f"expiry_{name_hash}", U64(0))
        if self._get_now() >= expiry:
            raise ContractError.DOMAIN_EXPIRED

        owner = self.storage.get(f"owner_{name_hash}")
        if owner is None:
            raise ContractError.DOMAIN_TAKEN # Not found
        return owner

    @view
    def reverse_resolve(self, address: Address) -> Bytes:
        """Reverse resolution: resolve address to primary domain name."""
        self._require_initialized()
        primary = self.storage.get(f"primary_{address}")
        if primary is None:
            return Bytes(b"")
        return primary

    @view
    def get_text_record(self, name: Bytes, key: Bytes) -> Bytes:
        """Query specific text record configuration."""
        name_hash = self.env.crypto().sha256(name)
        val = self.storage.get(f"text_{name_hash}_{key}")
        if val is None:
            return Bytes(b"")
        return val

    @view
    def get_domain_info(self, name: Bytes) -> Map:
        """Inspect domain expiry and ownership."""
        res = Map(self.env)
        name_hash = self.env.crypto().sha256(name)
        owner = self.storage.get(f"owner_{name_hash}")
        if owner is not None:
            res.set("owner", owner)
            res.set("expiry", self.storage.get(f"expiry_{name_hash}"))
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

    def _is_valid_name(self, name: Bytes) -> Bool:
        """Verify name does not contain dots (subdomains must use subdomain endpoint)."""
        for i in range(len(name)):
            # ASCII character 46 is '.'
            if name[i] == 46:
                return False
        return len(name) > 0

    def _require_active_owner(self, name_hash: Bytes, caller: Address):
        owner = self.storage.get(f"owner_{name_hash}")
        if owner is None or owner != caller:
            raise ContractError.UNAUTHORIZED

        expiry = self.storage.get(f"expiry_{name_hash}", U64(0))
        if self._get_now() >= expiry:
            raise ContractError.DOMAIN_EXPIRED
