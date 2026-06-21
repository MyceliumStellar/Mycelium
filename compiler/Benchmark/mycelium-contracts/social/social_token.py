"""
Social Token — Creator tokens with bonding curve pricing and token-gated content access.

Mycelium Smart Contract for Stellar
Allows content creators to launch individual creator tokens. Incorporates linear bonding curve
pricing, buy/sell mechanisms, protocol & creator fee allocations, and token-gated content validation.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    INVALID_PARAMETERS = 4
    CREATOR_ALREADY_REGISTERED = 5
    CREATOR_NOT_FOUND = 6
    INSUFFICIENT_FUNDS = 7
    CONTENT_NOT_FOUND = 8
    NO_ACCESS = 9
    TRANSFER_FAILED = 10


@contract
class SocialToken:
    """
    Creator Social Token platform with custom linear bonding curves,
    creator revenue share fee splits, and token-gated metadata checks.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        base_asset: Address,
        protocol_fee_bps: U64,
    ):
        """Initialize the Social Token platform."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if protocol_fee_bps > 10000:
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("admin", admin)
        self.storage.set("base_asset", base_asset)
        self.storage.set("protocol_fee_bps", protocol_fee_bps)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "base_asset": base_asset,
        })

    @external
    def set_protocol_fee(self, admin: Address, new_fee_bps: U64):
        """Update the global protocol fee in basis points."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        if new_fee_bps > 2000:  # Cap protocol fee at 20%
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("protocol_fee_bps", new_fee_bps)
        self.env.emit_event("protocol_fee_updated", {"fee_bps": new_fee_bps})

    @external
    def register_creator(
        self,
        creator: Address,
        token_name: Symbol,
        base_price: U128,
        slope: U128,
        creator_fee_bps: U64,
    ):
        """Register a creator token using a custom linear bonding curve (Price = base_price + slope * supply)."""
        creator.require_auth()
        self._require_initialized()

        if self.storage.get(f"creator:{creator}:registered", False):
            raise ContractError.CREATOR_ALREADY_REGISTERED

        if base_price == 0 or slope == 0 or creator_fee_bps > 5000:  # Max 50% creator fee
            raise ContractError.INVALID_PARAMETERS

        self.storage.set(f"creator:{creator}:registered", True)
        self.storage.set(f"creator:{creator}:name", token_name)
        self.storage.set(f"creator:{creator}:base_price", base_price)
        self.storage.set(f"creator:{creator}:slope", slope)
        self.storage.set(f"creator:{creator}:creator_fee_bps", creator_fee_bps)
        self.storage.set(f"creator:{creator}:supply", U128(0))
        self.storage.set(f"creator:{creator}:reserve", U128(0))

        self.env.emit_event("creator_registered", {
            "creator": creator,
            "token_name": token_name,
            "base_price": base_price,
            "slope": slope,
        })

    @external
    def buy_creator_token(
        self,
        buyer: Address,
        creator: Address,
        amount: U128,
    ):
        """Buy creator tokens using the linear bonding curve."""
        buyer.require_auth()
        self._require_initialized()

        if not self.storage.get(f"creator:{creator}:registered", False):
            raise ContractError.CREATOR_NOT_FOUND

        if amount == 0:
            raise ContractError.INVALID_PARAMETERS

        supply = self.storage.get(f"creator:{creator}:supply", U128(0))
        base_price = self.storage.get(f"creator:{creator}:base_price")
        slope = self.storage.get(f"creator:{creator}:slope")

        # Linear Bonding Curve Integral: Cost = Integral_{S}^{S+A} (base_price + slope * x) dx
        # Cost = base_price * amount + slope * (2 * supply * amount + amount * amount) / 2
        linear_part = base_price * amount
        quad_part = (slope * ((U128(2) * supply * amount) + (amount * amount))) // U128(2)
        curve_cost = linear_part + quad_part

        # Calculate fees
        creator_fee_bps = self.storage.get(f"creator:{creator}:creator_fee_bps")
        protocol_fee_bps = self.storage.get("protocol_fee_bps")

        creator_fee = (curve_cost * U128(creator_fee_bps)) // U128(10000)
        protocol_fee = (curve_cost * U128(protocol_fee_bps)) // U128(10000)
        total_cost = curve_cost + creator_fee + protocol_fee

        base_asset = self.storage.get("base_asset")

        # 1. Collect fees & reserves from buyer
        # Transfer creator fee directly to creator
        if creator_fee > 0:
            self.env.transfer(base_asset, buyer, creator, creator_fee)
        # Transfer protocol fee directly to admin
        if protocol_fee > 0:
            admin = self.storage.get("admin")
            self.env.transfer(base_asset, buyer, admin, protocol_fee)
        # Transfer curve cost to reserve pool inside contract
        self.env.transfer(base_asset, buyer, self.env.current_contract(), curve_cost)

        # 2. Update creator token states
        self.storage.set(f"creator:{creator}:supply", supply + amount)
        current_reserve = self.storage.get(f"creator:{creator}:reserve", U128(0))
        self.storage.set(f"creator:{creator}:reserve", current_reserve + curve_cost)

        # 3. Update buyer balance
        buyer_bal = self.storage.get(f"balance:{buyer}:{creator}", U128(0))
        self.storage.set(f"balance:{buyer}:{creator}", buyer_bal + amount)

        self.env.emit_event("tokens_bought", {
            "buyer": buyer,
            "creator": creator,
            "amount": amount,
            "cost": total_cost,
        })

    @external
    def sell_creator_token(
        self,
        seller: Address,
        creator: Address,
        amount: U128,
    ):
        """Sell creator tokens back to the bonding curve for the reserve asset."""
        seller.require_auth()
        self._require_initialized()

        if not self.storage.get(f"creator:{creator}:registered", False):
            raise ContractError.CREATOR_NOT_FOUND

        seller_bal = self.storage.get(f"balance:{seller}:{creator}", U128(0))
        if amount == 0 or amount > seller_bal:
            raise ContractError.INSUFFICIENT_FUNDS

        supply = self.storage.get(f"creator:{creator}:supply")
        base_price = self.storage.get(f"creator:{creator}:base_price")
        slope = self.storage.get(f"creator:{creator}:slope")

        # Integral_{S-A}^{S} (base_price + slope * x) dx
        # Curve refund = base_price * amount + slope * (2 * supply * amount - amount * amount) / 2
        linear_part = base_price * amount
        quad_part = (slope * ((U128(2) * supply * amount) - (amount * amount))) // U128(2)
        curve_refund = linear_part + quad_part

        # Deduct fees from refund
        creator_fee_bps = self.storage.get(f"creator:{creator}:creator_fee_bps")
        protocol_fee_bps = self.storage.get("protocol_fee_bps")

        creator_fee = (curve_refund * U128(creator_fee_bps)) // U128(10000)
        protocol_fee = (curve_refund * U128(protocol_fee_bps)) // U128(10000)
        net_refund = curve_refund - (creator_fee + protocol_fee)

        # 1. Update states
        self.storage.set(f"balance:{seller}:{creator}", seller_bal - amount)
        self.storage.set(f"creator:{creator}:supply", supply - amount)

        current_reserve = self.storage.get(f"creator:{creator}:reserve", U128(0))
        self.storage.set(f"creator:{creator}:reserve", current_reserve - curve_refund)

        # 2. Transfer net refund to seller, fees to creator and admin
        base_asset = self.storage.get("base_asset")
        if net_refund > 0:
            self.env.transfer(base_asset, self.env.current_contract(), seller, net_refund)
        if creator_fee > 0:
            self.env.transfer(base_asset, self.env.current_contract(), creator, creator_fee)
        if protocol_fee > 0:
            admin = self.storage.get("admin")
            self.env.transfer(base_asset, self.env.current_contract(), admin, protocol_fee)

        self.env.emit_event("tokens_sold", {
            "seller": seller,
            "creator": creator,
            "amount": amount,
            "refund": net_refund,
        })

    @external
    def publish_gated_content(
        self,
        creator: Address,
        content_hash: Bytes,
        min_token_balance: U128,
    ):
        """Creator publishes metadata / content hash gated by a minimum token threshold."""
        creator.require_auth()
        self._require_initialized()

        if not self.storage.get(f"creator:{creator}:registered", False):
            raise ContractError.CREATOR_NOT_FOUND

        self.storage.set(f"content:{creator}:{content_hash}:min_bal", min_token_balance)
        self.storage.set(f"content:{creator}:{content_hash}:exists", True)

        self.env.emit_event("content_published", {
            "creator": creator,
            "content_hash": content_hash,
            "min_balance": min_token_balance,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def check_content_access(self, user: Address, creator: Address, content_hash: Bytes) -> Bool:
        """Check if a user holds enough tokens to access a specific creator's gated content."""
        if not self.storage.get(f"content:{creator}:{content_hash}:exists", False):
            raise ContractError.CONTENT_NOT_FOUND

        min_bal = self.storage.get(f"content:{creator}:{content_hash}:min_bal")
        user_bal = self.storage.get(f"balance:{user}:{creator}", U128(0))

        return user_bal >= min_bal

    @view
    def get_creator_info(self, creator: Address) -> Map:
        """Get bonding curve details and current supply of creator."""
        if not self.storage.get(f"creator:{creator}:registered", False):
            raise ContractError.CREATOR_NOT_FOUND

        return {
            "name": self.storage.get(f"creator:{creator}:name"),
            "base_price": self.storage.get(f"creator:{creator}:base_price"),
            "slope": self.storage.get(f"creator:{creator}:slope"),
            "supply": self.storage.get(f"creator:{creator}:supply"),
            "reserve": self.storage.get(f"creator:{creator}:reserve"),
        }

    @view
    def get_token_balance(self, user: Address, creator: Address) -> U128:
        """Get user's balance of a specific creator's social token."""
        return self.storage.get(f"balance:{user}:{creator}", U128(0))

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED
