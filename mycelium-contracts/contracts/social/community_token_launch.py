"""
Community Token Launch — Smart contract for launching community tokens fairly.

Mycelium Smart Contract for Stellar
Features a multi-phase token sale:
1. Whitelist Phase with individual caps and custom whitelist registrations.
2. Public Phase with global and individual caps.
3. Lockup Incentives: Users can choose locking periods for bonus tokens.
4. Refund Period: If the soft cap is not met, or during a post-sale grace period,
   users can withdraw their contributions.
5. Distribution: Claiming tokens after the refund period, enforcing lockups if selected.
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
    PHASE_NOT_ACTIVE = 5
    EXCEEDS_CAP = 6
    NOT_WHITELISTED = 7
    HARD_CAP_REACHED = 8
    REFUND_NOT_ALLOWED = 9
    CLAIM_NOT_ALLOWED = 10
    ALREADY_CLAIMED = 11
    TOKENS_LOCKED = 12
    TRANSFER_FAILED = 13


# Sale phases
PHASE_PRE_SALE = 0
PHASE_WHITELIST = 1
PHASE_PUBLIC = 2
PHASE_REFUND = 3
PHASE_CLAIM = 4
PHASE_COMPLETED = 5


@contract
class CommunityTokenLaunch:
    """
    Manages fair launch distribution of a project token, verifying Whitelist phase caps,
    Public phase limits, and lockup options with token bonuses.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        launch_token: Address,
        base_asset: Address,
        price_per_token: U128,  # In terms of base asset
        soft_cap: U128,          # Minimum base asset needed
        hard_cap: U128,          # Maximum base asset accepted
        whitelist_start: U64,
        whitelist_end: U64,
        public_start: U64,
        public_end: U64,
        refund_end: U64,         # Grace period for refunds if soft cap met
        global_whitelist_cap: U128,  # Max base asset per user in Whitelist
        global_public_cap: U128,     # Max base asset per user in Public
    ):
        """Initialize the Community Token Launch parameters."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        # Time sanity checks
        if not (whitelist_start < whitelist_end <= public_start < public_end < refund_end):
            raise ContractError.INVALID_PARAMETERS

        if soft_cap == 0 or soft_cap > hard_cap or price_per_token == 0:
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("admin", admin)
        self.storage.set("launch_token", launch_token)
        self.storage.set("base_asset", base_asset)
        self.storage.set("price_per_token", price_per_token)
        self.storage.set("soft_cap", soft_cap)
        self.storage.set("hard_cap", hard_cap)
        self.storage.set("whitelist_start", whitelist_start)
        self.storage.set("whitelist_end", whitelist_end)
        self.storage.set("public_start", public_start)
        self.storage.set("public_end", public_end)
        self.storage.set("refund_end", refund_end)
        self.storage.set("global_whitelist_cap", global_whitelist_cap)
        self.storage.set("global_public_cap", global_public_cap)

        self.storage.set("total_raised", U128(0))
        self.storage.set("total_sold", U128(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "launch_token": launch_token,
            "base_asset": base_asset,
            "hard_cap": hard_cap,
        })

    @external
    def register_whitelist(self, admin: Address, user: Address, custom_cap: U128):
        """Register a user onto the whitelist with an optional custom cap."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        current_time = self.env.ledger().timestamp()
        whitelist_end = self.storage.get("whitelist_end")
        if current_time >= whitelist_end:
            raise ContractError.PHASE_NOT_ACTIVE

        self.storage.set(f"whitelist:is:{user}", True)
        self.storage.set(f"whitelist:cap:{user}", custom_cap)

        self.env.emit_event("whitelist_registered", {
            "user": user,
            "custom_cap": custom_cap,
        })

    @external
    def buy_tokens(self, buyer: Address, lockup_option: U64, base_amount: U128):
        """Contribute base assets to buy launch tokens, selecting a lockup option for bonuses."""
        buyer.require_auth()
        self._require_initialized()

        if base_amount == 0:
            raise ContractError.INVALID_PARAMETERS

        current_time = self.env.ledger().timestamp()
        phase = self._current_phase(current_time)

        if phase != PHASE_WHITELIST and phase != PHASE_PUBLIC:
            raise ContractError.PHASE_NOT_ACTIVE

        # Check caps
        total_raised = self.storage.get("total_raised")
        hard_cap = self.storage.get("hard_cap")
        if total_raised + base_amount > hard_cap:
            raise ContractError.HARD_CAP_REACHED

        # User contribution limits
        user_purchased = self.storage.get(f"purchased_base:{buyer}", U128(0))
        new_total = user_purchased + base_amount

        if phase == PHASE_WHITELIST:
            # Check whitelist membership
            if not self.storage.get(f"whitelist:is:{buyer}", False):
                raise ContractError.NOT_WHITELISTED

            # Check individual whitelist cap
            cap = self.storage.get(f"whitelist:cap:{buyer}", U128(0))
            if cap == 0:
                cap = self.storage.get("global_whitelist_cap")
            if new_total > cap:
                raise ContractError.EXCEEDS_CAP
        else:
            # Public phase cap check
            public_cap = self.storage.get("global_public_cap")
            if new_total > public_cap:
                raise ContractError.EXCEEDS_CAP

        # Calculate bonus multiplier based on lockup option
        # Lockup options:
        # 0: None
        # 1: 180 days -> 10% bonus tokens
        # 2: 360 days -> 25% bonus tokens
        bonus_pct = U128(100)
        lockup_duration = U64(0)
        if lockup_option == 1:
            bonus_pct = U128(110)
            lockup_duration = U64(180 * 24 * 60 * 60)
        elif lockup_option == 2:
            bonus_pct = U128(125)
            lockup_duration = U64(360 * 24 * 60 * 60)
        elif lockup_option != 0:
            raise ContractError.INVALID_PARAMETERS

        price = self.storage.get("price_per_token")
        
        # Calculate tokens to receive
        # tokens = (base_amount / price) * (bonus_pct / 100)
        token_amount = (base_amount * bonus_pct) // (price * U128(100))
        if token_amount == 0:
            raise ContractError.INVALID_PARAMETERS

        # Transfer base asset to contract
        base_asset = self.storage.get("base_asset")
        self.env.transfer(base_asset, buyer, self.env.current_contract(), base_amount)

        # Update stats
        self.storage.set("total_raised", total_raised + base_amount)
        total_sold = self.storage.get("total_sold", U128(0))
        self.storage.set("total_sold", total_sold + token_amount)

        # Update buyer records
        self.storage.set(f"purchased_base:{buyer}", new_total)
        user_tokens = self.storage.get(f"purchased_tokens:{buyer}", U128(0))
        self.storage.set(f"purchased_tokens:{buyer}", user_tokens + token_amount)
        
        # Set lockup details (use longest if buying multiple times)
        current_unlock = self.storage.get(f"unlock_time:{buyer}", U64(0))
        potential_unlock = current_time + lockup_duration
        if potential_unlock > current_unlock:
            self.storage.set(f"unlock_time:{buyer}", potential_unlock)

        self.env.emit_event("tokens_purchased", {
            "buyer": buyer,
            "base_amount": base_amount,
            "token_amount": token_amount,
            "lockup_option": lockup_option,
        })

    @external
    def refund_purchase(self, buyer: Address):
        """Allow a buyer to reclaim their base asset if sale fails or during the refund phase."""
        buyer.require_auth()
        self._require_initialized()

        current_time = self.env.ledger().timestamp()
        phase = self._current_phase(current_time)

        # Refunds are allowed if:
        # 1. We are in the refund grace period.
        # 2. Or, the sale ended but soft cap was not met.
        total_raised = self.storage.get("total_raised")
        soft_cap = self.storage.get("soft_cap")
        
        public_end = self.storage.get("public_end")
        soft_cap_failed = (current_time > public_end) and (total_raised < soft_cap)
        in_refund_phase = (phase == PHASE_REFUND)

        if not (soft_cap_failed or in_refund_phase):
            raise ContractError.REFUND_NOT_ALLOWED

        base_amount = self.storage.get(f"purchased_base:{buyer}", U128(0))
        if base_amount == 0:
            raise ContractError.INVALID_PARAMETERS

        token_amount = self.storage.get(f"purchased_tokens:{buyer}")

        # Clear state variables
        self.storage.set(f"purchased_base:{buyer}", U128(0))
        self.storage.set(f"purchased_tokens:{buyer}", U128(0))
        self.storage.set(f"unlock_time:{buyer}", U64(0))

        # Adjust totals
        self.storage.set("total_raised", total_raised - base_amount)
        total_sold = self.storage.get("total_sold")
        self.storage.set("total_sold", total_sold - token_amount)

        # Return base asset
        base_asset = self.storage.get("base_asset")
        self.env.transfer(base_asset, self.env.current_contract(), buyer, base_amount)

        self.env.emit_event("purchase_refunded", {
            "buyer": buyer,
            "refunded_base": base_amount,
            "canceled_tokens": token_amount,
        })

    @external
    def claim_tokens(self, buyer: Address):
        """Claim the purchased launch tokens after the refund period ends, respecting lockup restrictions."""
        buyer.require_auth()
        self._require_initialized()

        current_time = self.env.ledger().timestamp()
        phase = self._current_phase(current_time)

        if phase < PHASE_CLAIM:
            raise ContractError.CLAIM_NOT_ALLOWED

        # Verify soft cap was met
        total_raised = self.storage.get("total_raised")
        soft_cap = self.storage.get("soft_cap")
        if total_raised < soft_cap:
            raise ContractError.CLAIM_NOT_ALLOWED

        if self.storage.get(f"has_claimed:{buyer}", False):
            raise ContractError.ALREADY_CLAIMED

        token_amount = self.storage.get(f"purchased_tokens:{buyer}", U128(0))
        if token_amount == 0:
            raise ContractError.INVALID_PARAMETERS

        # Check lockup constraints
        unlock_time = self.storage.get(f"unlock_time:{buyer}", U64(0))
        if current_time < unlock_time:
            raise ContractError.TOKENS_LOCKED

        self.storage.set(f"has_claimed:{buyer}", True)

        # Distribute tokens
        launch_token = self.storage.get("launch_token")
        self.env.transfer(launch_token, self.env.current_contract(), buyer, token_amount)

        self.env.emit_event("tokens_claimed", {
            "buyer": buyer,
            "amount": token_amount,
        })

    @external
    def withdraw_funds(self, admin: Address):
        """Admin withdraws raised funds if soft cap met and refund grace period has ended."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        current_time = self.env.ledger().timestamp()
        phase = self._current_phase(current_time)

        if phase < PHASE_CLAIM:
            raise ContractError.CLAIM_NOT_ALLOWED

        total_raised = self.storage.get("total_raised")
        soft_cap = self.storage.get("soft_cap")
        if total_raised < soft_cap:
            raise ContractError.CLAIM_NOT_ALLOWED

        base_asset = self.storage.get("base_asset")
        # Withdraw the full amount currently in the contract's possession for base asset
        self.env.transfer(base_asset, self.env.current_contract(), admin, total_raised)

        self.env.emit_event("funds_withdrawn", {
            "admin": admin,
            "amount": total_raised,
        })

    @external
    def withdraw_unsold_tokens(self, admin: Address, recipient: Address):
        """Admin withdraws unsold tokens or unclaimed tokens after sale ends."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        current_time = self.env.ledger().timestamp()
        public_end = self.storage.get("public_end")
        if current_time <= public_end:
            raise ContractError.PHASE_NOT_ACTIVE

        # Calculate unsold amount
        # Ensure we keep locked/claimable tokens in the contract
        # Unsold = Contract balance - (total_sold - total_claimed_so_far)
        # However, a simpler check is comparing total sold against contract inventory,
        # or we just let admin withdraw what is NOT allocated to buyers.
        # Total tokens needed for buyers = total_sold (if soft_cap met)
        total_raised = self.storage.get("total_raised")
        soft_cap = self.storage.get("soft_cap")
        
        allocated = U128(0)
        if total_raised >= soft_cap:
            allocated = self.storage.get("total_sold")

        launch_token = self.storage.get("launch_token")
        
        # To avoid external balance queries, we track the initial supply or allow admin to
        # withdraw anything above `allocated`.
        # However, to be fully safe, let's say admin deposits a fixed pool size.
        # If they deposited 1,000,000 tokens and 600,000 were allocated, they can withdraw 400,000.
        # Let's track contract token deposit.
        total_tokens_deposited = self.storage.get("total_tokens_deposited", U128(0))
        withdrawn = self.storage.get("unsold_tokens_withdrawn", Bool(False))
        if withdrawn:
            raise ContractError.ALREADY_CLAIMED

        if total_tokens_deposited > allocated:
            unsold = total_tokens_deposited - allocated
            self.storage.set("unsold_tokens_withdrawn", True)
            self.env.transfer(launch_token, self.env.current_contract(), recipient, unsold)
            self.env.emit_event("unsold_tokens_withdrawn", {
                "recipient": recipient,
                "amount": unsold,
            })
        else:
            raise ContractError.INVALID_PARAMETERS

    @external
    def deposit_tokens(self, admin: Address, amount: U128):
        """Admin deposits the tokens to be sold/distributed."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        launch_token = self.storage.get("launch_token")
        self.env.transfer(launch_token, admin, self.env.current_contract(), amount)

        deposited = self.storage.get("total_tokens_deposited", U128(0))
        self.storage.set("total_tokens_deposited", deposited + amount)

        self.env.emit_event("tokens_deposited", {"amount": amount})

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_sale_info(self) -> Map:
        """Get the main sale parameters and progress."""
        return {
            "total_raised": self.storage.get("total_raised"),
            "total_sold": self.storage.get("total_sold"),
            "soft_cap": self.storage.get("soft_cap"),
            "hard_cap": self.storage.get("hard_cap"),
            "price_per_token": self.storage.get("price_per_token"),
            "deposited_tokens": self.storage.get("total_tokens_deposited", U128(0)),
        }

    @view
    def get_buyer_info(self, buyer: Address) -> Map:
        """Get details for a specific buyer."""
        return {
            "purchased_base": self.storage.get(f"purchased_base:{buyer}", U128(0)),
            "purchased_tokens": self.storage.get(f"purchased_tokens:{buyer}", U128(0)),
            "unlock_time": self.storage.get(f"unlock_time:{buyer}", U64(0)),
            "has_claimed": self.storage.get(f"has_claimed:{buyer}", False),
            "is_whitelisted": self.storage.get(f"whitelist:is:{buyer}", False),
        }

    @view
    def current_phase_info(self) -> U64:
        """Get the current phase index."""
        return U64(self._current_phase(self.env.ledger().timestamp()))

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _current_phase(self, time: U64) -> int:
        whitelist_start = self.storage.get("whitelist_start")
        whitelist_end = self.storage.get("whitelist_end")
        public_start = self.storage.get("public_start")
        public_end = self.storage.get("public_end")
        refund_end = self.storage.get("refund_end")

        if time < whitelist_start:
            return PHASE_PRE_SALE
        elif whitelist_start <= time < whitelist_end:
            return PHASE_WHITELIST
        elif public_start <= time < public_end:
            return PHASE_PUBLIC
        elif public_end <= time < refund_end:
            return PHASE_REFUND
        else:
            return PHASE_CLAIM
