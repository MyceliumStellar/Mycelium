"""
Loyalty Program — Merchant points program with epoch-based batches, stakes, and catalog redemptions.

Mycelium Smart Contract for Stellar
Enables merchants to run loyalty programs funded by staked collateral. Tracks point batches
with individual expiration limits, supports point transfers, and manages reward catalog redemptions.
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
    MERCHANT_NOT_REGISTERED = 5
    MERCHANT_ALREADY_EXISTS = 6
    ITEM_NOT_FOUND = 7
    ITEM_ALREADY_EXISTS = 8
    INSUFFICIENT_POINTS = 9
    TRANSFER_FAILED = 10
    INSUFFICIENT_STAKE = 11


@contract
class LoyaltyProgram:
    """
    Collateralized loyalty program contract with batch-based point tracking,
    expiration checks, and catalog reward redemptions.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        base_asset: Address,
        min_merchant_stake: U128,
        points_expiry_ledgers: U64,
    ):
        """Initialize the loyalty program contract."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if min_merchant_stake == 0 or points_expiry_ledgers == 0:
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("admin", admin)
        self.storage.set("base_asset", base_asset)
        self.storage.set("min_stake", min_merchant_stake)
        self.storage.set("points_expiry", points_expiry_ledgers)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "base_asset": base_asset,
        })

    @external
    def register_merchant(
        self,
        merchant: Address,
        points_per_token: U64,  # e.g., 10 points issued per 1 base token spent
        stake_amount: U128,
    ):
        """Register a merchant by depositing a safety stake to secure issued loyalty liabilities."""
        merchant.require_auth()
        self._require_initialized()

        if self.storage.get(f"merchant:{merchant}:registered", False):
            raise ContractError.MERCHANT_ALREADY_EXISTS

        min_stake = self.storage.get("min_stake")
        if stake_amount < min_stake or points_per_token == 0:
            raise ContractError.INVALID_PARAMETERS

        # Transfer merchant stake
        base_asset = self.storage.get("base_asset")
        self.env.transfer(base_asset, merchant, self.env.current_contract(), stake_amount)

        self.storage.set(f"merchant:{merchant}:registered", True)
        self.storage.set(f"merchant:{merchant}:points_rate", points_per_token)
        self.storage.set(f"merchant:{merchant}:stake", stake_amount)

        self.env.emit_event("merchant_registered", {
            "merchant": merchant,
            "points_rate": points_per_token,
            "stake": stake_amount,
        })

    @external
    def add_catalogue_item(
        self,
        merchant: Address,
        item_id: Symbol,
        points_cost: U64,
        item_hash: Bytes,
    ):
        """Add an item to the merchant's reward redemption catalogue."""
        merchant.require_auth()
        self._require_initialized()

        if not self.storage.get(f"merchant:{merchant}:registered", False):
            raise ContractError.MERCHANT_NOT_REGISTERED

        prefix = f"catalog:{merchant}:{item_id}"
        if self.storage.get(f"{prefix}:exists", False):
            raise ContractError.ITEM_ALREADY_EXISTS

        if points_cost == 0:
            raise ContractError.INVALID_PARAMETERS

        self.storage.set(f"{prefix}:exists", True)
        self.storage.set(f"{prefix}:cost", points_cost)
        self.storage.set(f"{prefix}:hash", item_hash)

        self.env.emit_event("catalogue_item_added", {
            "merchant": merchant,
            "item_id": item_id,
            "points_cost": points_cost,
        })

    @external
    def accrue_points(
        self,
        merchant: Address,
        user: Address,
        purchase_amount: U128,
    ):
        """Accrue loyalty points for a user purchase. Must be invoked by the registered merchant."""
        merchant.require_auth()
        self._require_initialized()

        if not self.storage.get(f"merchant:{merchant}:registered", False):
            raise ContractError.MERCHANT_NOT_REGISTERED

        if purchase_amount == 0:
            raise ContractError.INVALID_PARAMETERS

        points_rate = self.storage.get(f"merchant:{merchant}:points_rate")
        accrued_points = U64((purchase_amount * U128(points_rate)) // U128(1000000000)) # scaled by asset decimals
        if accrued_points == 0:
            accrued_points = U64(1)

        # Create point batch
        current_ledger = self.env.ledger().sequence()
        expiry_duration = self.storage.get("points_expiry")
        expiry_ledger = current_ledger + expiry_duration

        batch_idx = self.storage.get(f"batches_count:{merchant}:{user}", U64(0))

        prefix = f"batch:{merchant}:{user}:{batch_idx}"
        self.storage.set(f"{prefix}:amount", accrued_points)
        self.storage.set(f"{prefix}:spent", U64(0))
        self.storage.set(f"{prefix}:expiry", expiry_ledger)

        self.storage.set(f"batches_count:{merchant}:{user}", batch_idx + 1)

        self.env.emit_event("points_accrued", {
            "merchant": merchant,
            "user": user,
            "points": accrued_points,
            "expiry": expiry_ledger,
        })

    @external
    def redeem_reward(self, user: Address, merchant: Address, item_id: Symbol):
        """Redeem a catalog item by burning unexpired point batches."""
        user.require_auth()
        self._require_initialized()

        if not self.storage.get(f"merchant:{merchant}:registered", False):
            raise ContractError.MERCHANT_NOT_REGISTERED

        prefix = f"catalog:{merchant}:{item_id}"
        if not self.storage.get(f"{prefix}:exists", False):
            raise ContractError.ITEM_NOT_FOUND

        points_cost = self.storage.get(f"{prefix}:cost")
        current_ledger = self.env.ledger().sequence()

        # Check total active points and deduct them
        batches_count = self.storage.get(f"batches_count:{merchant}:{user}", U64(0))
        needed = points_cost

        # First pass: check total points availability
        available_total = U64(0)
        for idx in range(batches_count):
            batch_prefix = f"batch:{merchant}:{user}:{idx}"
            expiry = self.storage.get(f"{batch_prefix}:expiry", U64(0))
            if expiry > current_ledger:
                amount = self.storage.get(f"{batch_prefix}:amount", U64(0))
                spent = self.storage.get(f"{batch_prefix}:spent", U64(0))
                available_total += amount - spent

        if available_total < needed:
            raise ContractError.INSUFFICIENT_POINTS

        # Second pass: consume points from batches
        for idx in range(batches_count):
            batch_prefix = f"batch:{merchant}:{user}:{idx}"
            expiry = self.storage.get(f"{batch_prefix}:expiry", U64(0))
            if expiry > current_ledger:
                amount = self.storage.get(f"{batch_prefix}:amount", U64(0))
                spent = self.storage.get(f"{batch_prefix}:spent", U64(0))
                avail = amount - spent
                if avail > 0:
                    deduct = avail
                    if deduct > needed:
                        deduct = needed

                    self.storage.set(f"{batch_prefix}:spent", spent + deduct)
                    needed -= deduct
                    if needed == 0:
                        break

        self.env.emit_event("reward_redeemed", {
            "merchant": merchant,
            "user": user,
            "item_id": item_id,
            "cost": points_cost,
        })

    @external
    def transfer_points(
        self,
        sender: Address,
        recipient: Address,
        merchant: Address,
        points_to_transfer: U64,
    ):
        """Transfer points from sender to recipient, preserving the original expiration ledgers of point batches."""
        sender.require_auth()
        self._require_initialized()

        if sender == recipient or points_to_transfer == 0:
            raise ContractError.INVALID_PARAMETERS

        current_ledger = self.env.ledger().sequence()
        batches_count = self.storage.get(f"batches_count:{merchant}:{sender}", U64(0))

        # First pass: check total points availability
        available_total = U64(0)
        for idx in range(batches_count):
            batch_prefix = f"batch:{merchant}:{sender}:{idx}"
            expiry = self.storage.get(f"{batch_prefix}:expiry", U64(0))
            if expiry > current_ledger:
                amount = self.storage.get(f"{batch_prefix}:amount", U64(0))
                spent = self.storage.get(f"{batch_prefix}:spent", U64(0))
                available_total += amount - spent

        if available_total < points_to_transfer:
            raise ContractError.INSUFFICIENT_POINTS

        needed = points_to_transfer
        recipient_batches_count = self.storage.get(f"batches_count:{merchant}:{recipient}", U64(0))

        # Second pass: consume and recreate batches for recipient
        for idx in range(batches_count):
            batch_prefix = f"batch:{merchant}:{sender}:{idx}"
            expiry = self.storage.get(f"{batch_prefix}:expiry", U64(0))
            if expiry > current_ledger:
                amount = self.storage.get(f"{batch_prefix}:amount", U64(0))
                spent = self.storage.get(f"{batch_prefix}:spent", U64(0))
                avail = amount - spent
                if avail > 0:
                    deduct = avail
                    if deduct > needed:
                        deduct = needed

                    # Deduct from sender
                    self.storage.set(f"{batch_prefix}:spent", spent + deduct)

                    # Add new batch to recipient with original expiry
                    rec_prefix = f"batch:{merchant}:{recipient}:{recipient_batches_count}"
                    self.storage.set(f"{rec_prefix}:amount", deduct)
                    self.storage.set(f"{rec_prefix}:spent", U64(0))
                    self.storage.set(f"{rec_prefix}:expiry", expiry)
                    recipient_batches_count += 1

                    needed -= deduct
                    if needed == 0:
                        break

        self.storage.set(f"batches_count:{merchant}:{recipient}", recipient_batches_count)

        self.env.emit_event("points_transferred", {
            "merchant": merchant,
            "sender": sender,
            "recipient": recipient,
            "points": points_to_transfer,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_points_balance(self, user: Address, merchant: Address) -> U64:
        """Get the current unexpired points balance of a user."""
        current_ledger = self.env.ledger().sequence()
        batches_count = self.storage.get(f"batches_count:{merchant}:{user}", U64(0))

        active_points = U64(0)
        for idx in range(batches_count):
            batch_prefix = f"batch:{merchant}:{user}:{idx}"
            expiry = self.storage.get(f"{batch_prefix}:expiry", U64(0))
            if expiry > current_ledger:
                amount = self.storage.get(f"{batch_prefix}:amount", U64(0))
                spent = self.storage.get(f"{batch_prefix}:spent", U64(0))
                active_points += amount - spent

        return active_points

    @view
    def get_catalogue_item(self, merchant: Address, item_id: Symbol) -> Map:
        """Retrieve details of catalogue item."""
        prefix = f"catalog:{merchant}:{item_id}"
        if not self.storage.get(f"{prefix}:exists", False):
            raise ContractError.ITEM_NOT_FOUND

        return {
            "cost": self.storage.get(f"{prefix}:cost"),
            "hash": self.storage.get(f"{prefix}:hash"),
        }

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
