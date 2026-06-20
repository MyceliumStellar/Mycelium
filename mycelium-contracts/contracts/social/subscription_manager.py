"""
Subscription Manager — Tiered memberships, recurring payments, grace periods, and creator payouts.

Mycelium Smart Contract for Stellar
Manages creators' tiered membership packages, pre-authorized user deposit vaults for
recurring billings, renewal grace periods, discount vouchers, and creator earnings withdrawals.
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
    TIER_NOT_FOUND = 5
    TIER_ALREADY_EXISTS = 6
    INSUFFICIENT_BILLING_BALANCE = 7
    VOUCHER_EXPIRED = 8
    VOUCHER_EXHAUSTED = 9
    SUBSCRIPTION_NOT_ACTIVE = 10
    INSUFFICIENT_FUNDS = 11


@contract
class SubscriptionManager:
    """
    Tiered membership manager using user deposit vaults for pre-authorized bills,
    grace periods, voucher validation, and creator payouts.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        base_asset: Address,
        platform_fee_bps: U64,
        grace_period_ledgers: U64,
        subscription_period_ledgers: U64,
    ):
        """Initialize the subscription manager contract."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if platform_fee_bps > 2000 or grace_period_ledgers == 0 or subscription_period_ledgers == 0:
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("admin", admin)
        self.storage.set("base_asset", base_asset)
        self.storage.set("platform_fee", platform_fee_bps)
        self.storage.set("grace_period", grace_period_ledgers)
        self.storage.set("subscription_period", subscription_period_ledgers)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "base_asset": base_asset,
        })

    @external
    def register_tier(
        self,
        creator: Address,
        tier_id: U64,
        cost_per_period: U128,
    ):
        """Register a membership tier for a content creator."""
        creator.require_auth()
        self._require_initialized()

        if cost_per_period == 0:
            raise ContractError.INVALID_PARAMETERS

        if self.storage.get(f"creator:{creator}:tier:{tier_id}:registered", False):
            raise ContractError.TIER_ALREADY_EXISTS

        self.storage.set(f"creator:{creator}:tier:{tier_id}:registered", True)
        self.storage.set(f"creator:{creator}:tier:{tier_id}:cost", cost_per_period)

        self.env.emit_event("tier_registered", {
            "creator": creator,
            "tier_id": tier_id,
            "cost": cost_per_period,
        })

    @external
    def register_voucher(
        self,
        creator: Address,
        code: Symbol,
        discount_bps: U64,
        max_uses: U64,
        expiry_ledger: U64,
    ):
        """Register a discount voucher for a creator's tiers."""
        creator.require_auth()
        self._require_initialized()

        if discount_bps == 0 or discount_bps > 10000 or max_uses == 0:
            raise ContractError.INVALID_PARAMETERS

        prefix = f"voucher:{creator}:{code}"
        self.storage.set(f"{prefix}:registered", True)
        self.storage.set(f"{prefix}:discount", discount_bps)
        self.storage.set(f"{prefix}:max_uses", max_uses)
        self.storage.set(f"{prefix}:uses", U64(0))
        self.storage.set(f"{prefix}:expiry", expiry_ledger)

        self.env.emit_event("voucher_registered", {
            "creator": creator,
            "code": code,
            "discount_bps": discount_bps,
            "expiry": expiry_ledger,
        })

    @external
    def deposit_billing_balance(self, user: Address, amount: U128):
        """Deposit base assets into the user's pre-authorized billing balance vault."""
        user.require_auth()
        self._require_initialized()

        if amount == 0:
            raise ContractError.INVALID_PARAMETERS

        base_asset = self.storage.get("base_asset")
        self.env.transfer(base_asset, user, self.env.current_contract(), amount)

        current_bal = self.storage.get(f"billing_vault:{user}", U128(0))
        self.storage.set(f"billing_vault:{user}", current_bal + amount)

        self.env.emit_event("billing_deposited", {
            "user": user,
            "amount": amount,
        })

    @external
    def withdraw_billing_balance(self, user: Address, amount: U128):
        """Withdraw unused pre-authorized billing funds back to user address."""
        user.require_auth()
        self._require_initialized()

        current_bal = self.storage.get(f"billing_vault:{user}", U128(0))
        if amount > current_bal:
            raise ContractError.INSUFFICIENT_BILLING_BALANCE

        self.storage.set(f"billing_vault:{user}", current_bal - amount)

        base_asset = self.storage.get("base_asset")
        self.env.transfer(base_asset, self.env.current_contract(), user, amount)

        self.env.emit_event("billing_withdrawn", {
            "user": user,
            "amount": amount,
        })

    @external
    def subscribe_or_renew(
        self,
        subscriber: Address,
        creator: Address,
        tier_id: U64,
        voucher_code: Symbol,
    ):
        """Subscribe or renew a membership, drawing from the user's billing vault. Supports vouchers and grace periods."""
        subscriber.require_auth()
        self._require_initialized()

        if not self.storage.get(f"creator:{creator}:tier:{tier_id}:registered", False):
            raise ContractError.TIER_NOT_FOUND

        base_cost = self.storage.get(f"creator:{creator}:tier:{tier_id}:cost")

        # Apply Discount Voucher if provided
        discount_bps = U64(0)
        if len(str(voucher_code)) > 0 and self.storage.get(f"voucher:{creator}:{voucher_code}:registered", False):
            prefix = f"voucher:{creator}:{voucher_code}"
            expiry = self.storage.get(f"{prefix}:expiry")
            uses = self.storage.get(f"{prefix}:uses", U64(0))
            max_uses = self.storage.get(f"{prefix}:max_uses")

            current_ledger = self.env.ledger().sequence()
            if current_ledger <= expiry and uses < max_uses:
                discount_bps = self.storage.get(f"{prefix}:discount")
                self.storage.set(f"{prefix}:uses", uses + 1)

        final_cost = base_cost
        if discount_bps > 0:
            discount = (base_cost * U128(discount_bps)) // U128(10000)
            final_cost = base_cost - discount

        billing_bal = self.storage.get(f"billing_vault:{subscriber}", U128(0))
        if billing_bal < final_cost:
            raise ContractError.INSUFFICIENT_BILLING_BALANCE

        # Deduct from user vault
        self.storage.set(f"billing_vault:{subscriber}", billing_bal - final_cost)

        # Distribute: platform fee vs creator payout
        fee_bps = self.storage.get("platform_fee")
        platform_fee = (final_cost * U128(fee_bps)) // U128(10000)
        creator_share = final_cost - platform_fee

        # Credit creator balance inside contract
        creator_bal = self.storage.get(f"creator_balance:{creator}", U128(0))
        self.storage.set(f"creator_balance:{creator}", creator_bal + creator_share)

        if platform_fee > 0:
            admin = self.storage.get("admin")
            base_asset = self.storage.get("base_asset")
            self.env.transfer(base_asset, self.env.current_contract(), admin, platform_fee)

        # Calculate new expiration ledger sequence
        current_ledger = self.env.ledger().sequence()
        sub_key = f"membership:{subscriber}:{creator}"
        old_expiry = self.storage.get(f"{sub_key}:expiry", U64(0))
        grace_period = self.storage.get("grace_period")

        period = self.storage.get("subscription_period")
        new_expiry = U64(0)

        # If subscriber is active or within grace period, renew starting from old expiry
        if old_expiry > 0 and current_ledger <= old_expiry + grace_period:
            new_expiry = old_expiry + period
        else:
            # If expired beyond grace period (lapsed), start new subscription from now
            new_expiry = current_ledger + period

        self.storage.set(f"{sub_key}:expiry", new_expiry)
        self.storage.set(f"{sub_key}:tier", tier_id)

        self.env.emit_event("subscription_updated", {
            "subscriber": subscriber,
            "creator": creator,
            "tier_id": tier_id,
            "cost": final_cost,
            "expiry": new_expiry,
        })

    @external
    def withdraw_creator_earnings(self, creator: Address, amount: U128):
        """Allow creators to withdraw their accumulated subscription earnings."""
        creator.require_auth()
        self._require_initialized()

        earnings = self.storage.get(f"creator_balance:{creator}", U128(0))
        if amount > earnings:
            raise ContractError.INSUFFICIENT_FUNDS

        self.storage.set(f"creator_balance:{creator}", earnings - amount)

        base_asset = self.storage.get("base_asset")
        self.env.transfer(base_asset, self.env.current_contract(), creator, amount)

        self.env.emit_event("earnings_withdrawn", {
            "creator": creator,
            "amount": amount,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_subscription(self, subscriber: Address, creator: Address) -> Map:
        """Check user's subscription tier, expiry, and active status including grace period."""
        sub_key = f"membership:{subscriber}:{creator}"
        expiry = self.storage.get(f"{sub_key}:expiry", U64(0))
        tier = self.storage.get(f"{sub_key}:tier", U64(0))

        current_ledger = self.env.ledger().sequence()
        grace_period = self.storage.get("grace_period")

        # Active if sequence is under expiry.
        # Within grace period: sequence is between expiry and expiry + grace_period
        is_active = current_ledger <= expiry
        is_in_grace = (current_ledger > expiry) and (current_ledger <= expiry + grace_period)

        return {
            "tier_id": tier,
            "expiry": expiry,
            "is_active": is_active or is_in_grace,
            "grace_active": is_in_grace,
        }

    @view
    def get_vault_balance(self, user: Address) -> U128:
        """Get pre-authorized billing balance of a user."""
        return self.storage.get(f"billing_vault:{user}", U128(0))

    @view
    def get_creator_earnings(self, creator: Address) -> U128:
        """Get accumulated earnings of a creator."""
        return self.storage.get(f"creator_balance:{creator}", U128(0))

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED
