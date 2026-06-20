"""
Content Platform — Post publishing, tip splitting, creator subscriptions, and curation rewards.

Mycelium Smart Contract for Stellar
Provides publishing, premium subscription gating, tip splitting between authors,
co-authors, and early curators (curation rewards), and a moderation flag system.
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
    POST_NOT_FOUND = 5
    POST_HIDDEN = 6
    SUBSCRIBER_ONLY = 7
    ALREADY_SUBSCRIBED = 8
    ALREADY_CURATED = 9
    CURATION_LIMIT_REACHED = 10
    TRANSFER_FAILED = 11


@contract
class ContentPlatform:
    """
    Decentralized social content contract supporting author/co-author tip splits,
    curator reward distribution pools, and premium creator subscription vaults.
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
    ):
        """Initialize the Content Platform."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if platform_fee_bps > 2000:
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("admin", admin)
        self.storage.set("base_asset", base_asset)
        self.storage.set("platform_fee", platform_fee_bps)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "base_asset": base_asset,
        })

    @external
    def set_subscription_rate(self, creator: Address, rate_per_ledger: U128):
        """Set the creator's subscription price in base assets per ledger duration."""
        creator.require_auth()
        self._require_initialized()

        self.storage.set(f"creator:{creator}:sub_rate", rate_per_ledger)
        self.env.emit_event("subscription_rate_updated", {
            "creator": creator,
            "rate": rate_per_ledger,
        })

    @external
    def publish_post(
        self,
        author: Address,
        post_id: Symbol,
        content_hash: Bytes,
        is_premium: Bool,
        co_author: Address,
        co_author_share_bps: U64,
    ):
        """Publish a post with custom metadata, premium flag, and optional co-author splits."""
        author.require_auth()
        self._require_initialized()

        if co_author_share_bps > 10000:
            raise ContractError.INVALID_PARAMETERS

        prefix = f"post:{author}:{post_id}"
        self.storage.set(f"{prefix}:content", content_hash)
        self.storage.set(f"{prefix}:premium", is_premium)
        self.storage.set(f"{prefix}:co_author", co_author)
        self.storage.set(f"{prefix}:co_author_share", co_author_share_bps)
        self.storage.set(f"{prefix}:curators", Vec())
        self.storage.set(f"{prefix}:hidden", False)
        self.storage.set(f"{prefix}:exists", True)

        self.env.emit_event("post_published", {
            "author": author,
            "post_id": post_id,
            "premium": is_premium,
            "co_author": co_author,
        })

    @external
    def subscribe_to_creator(
        self,
        subscriber: Address,
        creator: Address,
        duration_ledgers: U64,
    ):
        """Subscribe to a creator to unlock all premium posts for a duration of ledgers."""
        subscriber.require_auth()
        self._require_initialized()

        rate = self.storage.get(f"creator:{creator}:sub_rate", None)
        if rate is None or rate == 0 or duration_ledgers == 0:
            raise ContractError.INVALID_PARAMETERS

        total_cost = rate * U128(duration_ledgers)
        base_asset = self.storage.get("base_asset")

        # Collect fee: split into platform fee and creator payout
        fee_bps = self.storage.get("platform_fee")
        platform_fee = (total_cost * U128(fee_bps)) // U128(10000)
        creator_payout = total_cost - platform_fee

        # Transfer funds
        if creator_payout > 0:
            self.env.transfer(base_asset, subscriber, creator, creator_payout)
        if platform_fee > 0:
            admin = self.storage.get("admin")
            self.env.transfer(base_asset, subscriber, admin, platform_fee)

        # Update subscriber expiry
        current_expiry = self.storage.get(f"subscription:{subscriber}:{creator}", U64(0))
        current_ledger = self.env.ledger().sequence()

        start_ledger = current_ledger
        if current_expiry > current_ledger:
            start_ledger = current_expiry

        new_expiry = start_ledger + duration_ledgers
        self.storage.set(f"subscription:{subscriber}:{creator}", new_expiry)

        self.env.emit_event("creator_subscribed", {
            "subscriber": subscriber,
            "creator": creator,
            "expiry": new_expiry,
        })

    @external
    def curate_post(self, curator: Address, author: Address, post_id: Symbol):
        """Upvote/curate a post early to qualify for curation rewards (share of subsequent tips)."""
        curator.require_auth()
        self._require_initialized()

        prefix = f"post:{author}:{post_id}"
        if not self.storage.get(f"{prefix}:exists", False):
            raise ContractError.POST_NOT_FOUND

        if self.storage.get(f"{prefix}:hidden", False):
            raise ContractError.POST_HIDDEN

        curators = self.storage.get(f"{prefix}:curators")
        # Max 5 curators receive rewards to prevent diluted payout arrays
        if len(curators) >= 5:
            raise ContractError.CURATION_LIMIT_REACHED

        # Check if already curated
        for idx in range(len(curators)):
            if curators[idx] == curator:
                raise ContractError.ALREADY_CURATED

        curators.append(curator)
        self.storage.set(f"{prefix}:curators", curators)

        self.env.emit_event("post_curated", {
            "post_id": post_id,
            "author": author,
            "curator": curator,
            "index": len(curators),
        })

    @external
    def tip_post(
        self,
        tipper: Address,
        author: Address,
        post_id: Symbol,
        amount: U128,
    ):
        """Tip a post. Splits amount: platform fee, curation pool (5%), and author/co-author shares."""
        tipper.require_auth()
        self._require_initialized()

        prefix = f"post:{author}:{post_id}"
        if not self.storage.get(f"{prefix}:exists", False):
            raise ContractError.POST_NOT_FOUND

        if self.storage.get(f"{prefix}:hidden", False):
            raise ContractError.POST_HIDDEN

        # Gating Check: if premium, sender must be subscribed
        is_premium = self.storage.get(f"{prefix}:premium", False)
        if is_premium and tipper != author:
            expiry = self.storage.get(f"subscription:{tipper}:{author}", U64(0))
            if self.env.ledger().sequence() > expiry:
                raise ContractError.SUBSCRIBER_ONLY

        base_asset = self.storage.get("base_asset")
        admin = self.storage.get("admin")

        # 1. Deduct Platform Fee
        fee_bps = self.storage.get("platform_fee")
        platform_fee = (amount * U128(fee_bps)) // U128(10000)
        remaining_payout = amount - platform_fee

        if platform_fee > 0:
            self.env.transfer(base_asset, tipper, admin, platform_fee)

        # 2. Curation Pool (5% of tip amount shared among early curators)
        curators = self.storage.get(f"{prefix}:curators")
        curators_count = len(curators)
        curator_total_payout = U128(0)

        if curators_count > 0:
            curator_total_payout = (remaining_payout * U128(500)) // U128(10000)  # 5%
            share = curator_total_payout // U128(curators_count)
            for idx in range(curators_count):
                curator_addr = curators[idx]
                if share > 0:
                    self.env.transfer(base_asset, tipper, curator_addr, share)
            # Subtract what was actually paid out
            remaining_payout -= share * U128(curators_count)

        # 3. Split between Author and Co-author
        co_author = self.storage.get(f"{prefix}:co_author")
        co_author_share_bps = self.storage.get(f"{prefix}:co_author_share")

        co_author_payout = U128(0)
        if co_author != author and co_author_share_bps > 0:
            co_author_payout = (remaining_payout * U128(co_author_share_bps)) // U128(10000)
            if co_author_payout > 0:
                self.env.transfer(base_asset, tipper, co_author, co_author_payout)

        author_payout = remaining_payout - co_author_payout
        if author_payout > 0:
            self.env.transfer(base_asset, tipper, author, author_payout)

        self.env.emit_event("post_tipped", {
            "post_id": post_id,
            "author": author,
            "tipper": tipper,
            "amount": amount,
            "author_share": author_payout,
            "co_author_share": co_author_payout,
            "curator_share": curator_total_payout,
        })

    @external
    def report_post(
        self,
        reporter: Address,
        author: Address,
        post_id: Symbol,
        reason: Bytes,
    ):
        """Flag content/post for platform rules violations."""
        reporter.require_auth()
        self._require_initialized()

        prefix = f"post:{author}:{post_id}"
        if not self.storage.get(f"{prefix}:exists", False):
            raise ContractError.POST_NOT_FOUND

        # Log flag reason
        self.storage.set(f"{prefix}:flagged:{reporter}", True)
        self.env.emit_event("post_flagged", {
            "post_id": post_id,
            "author": author,
            "reporter": reporter,
            "reason": reason,
        })

    @external
    def moderate_post(
        self,
        admin: Address,
        author: Address,
        post_id: Symbol,
        hide: Bool,
    ):
        """Freeze or hide a post violating community guidelines."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        prefix = f"post:{author}:{post_id}"
        if not self.storage.get(f"{prefix}:exists", False):
            raise ContractError.POST_NOT_FOUND

        self.storage.set(f"{prefix}:hidden", hide)
        self.env.emit_event("post_moderated", {
            "post_id": post_id,
            "author": author,
            "hidden": hide,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_post(self, author: Address, post_id: Symbol) -> Map:
        """Get public post parameters and configuration."""
        prefix = f"post:{author}:{post_id}"
        if not self.storage.get(f"{prefix}:exists", False):
            raise ContractError.POST_NOT_FOUND

        return {
            "content": self.storage.get(f"{prefix}:content"),
            "premium": self.storage.get(f"{prefix}:premium"),
            "co_author": self.storage.get(f"{prefix}:co_author"),
            "co_author_share": self.storage.get(f"{prefix}:co_author_share"),
            "curators": self.storage.get(f"{prefix}:curators"),
            "hidden": self.storage.get(f"{prefix}:hidden"),
        }

    @view
    def get_subscription_status(self, subscriber: Address, creator: Address) -> Map:
        """Retrieve membership subscription expiry status."""
        expiry = self.storage.get(f"subscription:{subscriber}:{creator}", U64(0))
        current_ledger = self.env.ledger().sequence()
        return {
            "expiry": expiry,
            "active": expiry > current_ledger,
        }

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED
