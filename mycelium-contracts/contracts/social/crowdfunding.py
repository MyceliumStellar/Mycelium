"""
Crowdfunding — Goal-based funding with milestone schedules and backer voting.

Mycelium Smart Contract for Stellar
Implements milestone-locked campaign crowdfunding. Features target goals, contribution
rewards, progress reports, backer-weighted milestone approval votes, and fail-safe refund triggers.
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
    CAMPAIGN_NOT_FOUND = 5
    CAMPAIGN_EXPIRED = 6
    CAMPAIGN_ACTIVE = 7
    CAMPAIGN_FAILED = 8
    CAMPAIGN_SUCCESS = 9
    GOAL_NOT_REACHED = 10
    MILESTONE_VOTE_ACTIVE = 11
    NO_VOTE_ACTIVE = 12
    VOTE_PERIOD_CLOSED = 13
    VOTE_PERIOD_ACTIVE = 14
    TRANSFER_FAILED = 15
    REWARDS_EXHAUSTED = 16
    ALREADY_VOTED = 17


class CampaignState:
    FUNDING = 1
    SUCCESS = 2
    FAILED = 3
    REFUNDING = 4


@contract
class Crowdfunding:
    """
    Crowdfunding contract with backer-weighted milestone approvals,
    re-entraint refund protection, and contribution reward levels.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        asset_token: Address,
    ):
        """Initialize the crowdfunding contract."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("asset_token", asset_token)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "asset_token": asset_token,
        })

    @external
    def create_campaign(
        self,
        creator: Address,
        campaign_id: Symbol,
        target_goal: U128,
        deadline_ledger: U64,
        milestone_bps_splits: Vec,  # e.g., [3000, 4000, 3000] representing 30%, 40%, 30% releases
    ):
        """Create a new crowdfunding campaign with milestone-gated release schedules."""
        creator.require_auth()
        self._require_initialized()

        if self.storage.get(f"campaign:{campaign_id}:exists", False):
            raise ContractError.INVALID_PARAMETERS

        current_ledger = self.env.ledger().sequence()
        if deadline_ledger <= current_ledger + U64(2000) or target_goal == 0:
            raise ContractError.INVALID_PARAMETERS

        # Validate milestones splits (must sum to exactly 10000 bps)
        milestones_count = len(milestone_bps_splits)
        if milestones_count == 0:
            raise ContractError.INVALID_PARAMETERS

        total_bps = U64(0)
        for i in range(milestones_count):
            total_bps += milestone_bps_splits[i]

        if total_bps != 10000:
            raise ContractError.INVALID_PARAMETERS

        # Save campaign details
        prefix = f"campaign:{campaign_id}"
        self.storage.set(f"{prefix}:exists", True)
        self.storage.set(f"{prefix}:creator", creator)
        self.storage.set(f"{prefix}:target", target_goal)
        self.storage.set(f"{prefix}:deadline", deadline_ledger)
        self.storage.set(f"{prefix}:state", CampaignState.FUNDING)
        self.storage.set(f"{prefix}:total_pledged", U128(0))
        self.storage.set(f"{prefix}:milestones_count", U64(milestones_count))
        self.storage.set(f"{prefix}:current_milestone", U64(0))
        self.storage.set(f"{prefix}:unreleased_pool", U128(0))

        # Store milestone percentages
        for idx in range(milestones_count):
            self.storage.set(f"{prefix}:milestone:{idx}:bps", milestone_bps_splits[idx])

        self.env.emit_event("campaign_created", {
            "campaign_id": campaign_id,
            "creator": creator,
            "target": target_goal,
            "deadline": deadline_ledger,
        })

    @external
    def pledge(
        self,
        backer: Address,
        campaign_id: Symbol,
        amount: U128,
    ):
        """Pledge funds to an active campaign. Locks backing assets in contract."""
        backer.require_auth()
        self._require_initialized()

        prefix = f"campaign:{campaign_id}"
        if not self.storage.get(f"{prefix}:exists", False):
            raise ContractError.CAMPAIGN_NOT_FOUND

        state = self.storage.get(f"{prefix}:state")
        if state != CampaignState.FUNDING:
            raise ContractError.CAMPAIGN_FAILED

        current_ledger = self.env.ledger().sequence()
        deadline = self.storage.get(f"{prefix}:deadline")
        if current_ledger > deadline:
            raise ContractError.CAMPAIGN_EXPIRED

        if amount == 0:
            raise ContractError.INVALID_PARAMETERS

        # Transfer tokens to escrow
        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, backer, self.env.current_contract(), amount)

        # Update pledges
        pledge_key = f"pledge:{campaign_id}:{backer}"
        current_pledge = self.storage.get(pledge_key, U128(0))
        self.storage.set(pledge_key, current_pledge + amount)

        total_pledged = self.storage.get(f"{prefix}:total_pledged", U128(0))
        new_total_pledged = total_pledged + amount
        self.storage.set(f"{prefix}:total_pledged", new_total_pledged)

        self.env.emit_event("pledge_made", {
            "campaign_id": campaign_id,
            "backer": backer,
            "amount": amount,
            "total_pledged": new_total_pledged,
        })

    @external
    def check_campaign_outcome(self, caller: Address, campaign_id: Symbol):
        """Finalize campaign outcome based on target goal achievement at deadline."""
        caller.require_auth()
        self._require_initialized()

        prefix = f"campaign:{campaign_id}"
        if not self.storage.get(f"{prefix}:exists", False):
            raise ContractError.CAMPAIGN_NOT_FOUND

        state = self.storage.get(f"{prefix}:state")
        if state != CampaignState.FUNDING:
            raise ContractError.INVALID_PARAMETERS

        current_ledger = self.env.ledger().sequence()
        deadline = self.storage.get(f"{prefix}:deadline")
        if current_ledger <= deadline:
            raise ContractError.CAMPAIGN_ACTIVE

        total_pledged = self.storage.get(f"{prefix}:total_pledged", U128(0))
        target = self.storage.get(f"{prefix}:target")

        if total_pledged >= target:
            self.storage.set(f"{prefix}:state", CampaignState.SUCCESS)
            # Fund unreleased pool
            self.storage.set(f"{prefix}:unreleased_pool", total_pledged)
            self.env.emit_event("campaign_finalized_success", {
                "campaign_id": campaign_id,
                "total_pledged": total_pledged,
            })
        else:
            self.storage.set(f"{prefix}:state", CampaignState.FAILED)
            self.env.emit_event("campaign_finalized_failed", {
                "campaign_id": campaign_id,
                "total_pledged": total_pledged,
            })

    @external
    def claim_refund(self, backer: Address, campaign_id: Symbol):
        """Claim a full refund if the campaign failed to reach its target goal by deadline."""
        backer.require_auth()
        self._require_initialized()

        prefix = f"campaign:{campaign_id}"
        if not self.storage.get(f"{prefix}:exists", False):
            raise ContractError.CAMPAIGN_NOT_FOUND

        state = self.storage.get(f"{prefix}:state")
        # Allow refunding if campaign failed or project was canceled/refunded in milestone stage
        if state != CampaignState.FAILED and state != CampaignState.REFUNDING:
            raise ContractError.CAMPAIGN_SUCCESS

        pledge_key = f"pledge:{campaign_id}:{backer}"
        backer_pledge = self.storage.get(pledge_key, U128(0))
        if backer_pledge == 0:
            raise ContractError.INVALID_PARAMETERS

        refund_amount = backer_pledge
        if state == CampaignState.REFUNDING:
            # If project failed halfway during milestones, backer gets refunded their proportional
            # share of the remaining unreleased pool.
            total_pledged = self.storage.get(f"{prefix}:total_pledged")
            unreleased = self.storage.get(f"{prefix}:unreleased_pool")
            # refund = backer_pledge * unreleased / total_pledged
            refund_amount = (backer_pledge * unreleased) // total_pledged

        # Reset pledge to prevent double refunds
        self.storage.set(pledge_key, U128(0))

        if refund_amount > 0:
            asset_token = self.storage.get("asset_token")
            self.env.transfer(asset_token, self.env.current_contract(), backer, refund_amount)

        self.env.emit_event("refund_claimed", {
            "campaign_id": campaign_id,
            "backer": backer,
            "refund_amount": refund_amount,
        })

    @external
    def propose_milestone_release(
        self,
        creator: Address,
        campaign_id: Symbol,
        report_hash: Bytes,
        voting_duration: U64,
    ):
        """Creator uploads progress report and triggers a milestone release vote."""
        creator.require_auth()
        self._require_initialized()

        prefix = f"campaign:{campaign_id}"
        if not self.storage.get(f"{prefix}:exists", False):
            raise ContractError.CAMPAIGN_NOT_FOUND

        state = self.storage.get(f"{prefix}:state")
        if state != CampaignState.SUCCESS:
            raise ContractError.CAMPAIGN_FAILED

        # Verify no vote is currently active
        if self.storage.get(f"{prefix}:vote_active", False):
            raise ContractError.MILESTONE_VOTE_ACTIVE

        current_milestone = self.storage.get(f"{prefix}:current_milestone")
        max_milestones = self.storage.get(f"{prefix}:milestones_count")
        if current_milestone >= max_milestones:
            raise ContractError.INVALID_PARAMETERS

        # Set up voting window
        current_ledger = self.env.ledger().sequence()
        voting_end = current_ledger + voting_duration

        self.storage.set(f"{prefix}:vote_active", True)
        self.storage.set(f"{prefix}:vote_end", voting_end)
        self.storage.set(f"{prefix}:vote_report", report_hash)
        self.storage.set(f"{prefix}:vote_yes", U128(0))
        self.storage.set(f"{prefix}:vote_no", U128(0))

        self.env.emit_event("milestone_proposed", {
            "campaign_id": campaign_id,
            "milestone_index": current_milestone,
            "report_hash": report_hash,
            "voting_end": voting_end,
        })

    @external
    def vote_milestone(
        self,
        backer: Address,
        campaign_id: Symbol,
        approve: Bool,
    ):
        """Cast backer-weighted vote on the proposed milestone release."""
        backer.require_auth()
        self._require_initialized()

        prefix = f"campaign:{campaign_id}"
        if not self.storage.get(f"{prefix}:vote_active", False):
            raise ContractError.NO_VOTE_ACTIVE

        current_ledger = self.env.ledger().sequence()
        vote_end = self.storage.get(f"{prefix}:vote_end")
        if current_ledger > vote_end:
            raise ContractError.VOTE_PERIOD_CLOSED

        vote_record_key = f"vote_milestone:{campaign_id}:{backer}"
        if self.storage.get(vote_record_key, False):
            raise ContractError.ALREADY_VOTED

        pledge_weight = self.storage.get(f"pledge:{campaign_id}:{backer}", U128(0))
        if pledge_weight == 0:
            raise ContractError.UNAUTHORIZED

        self.storage.set(vote_record_key, True)

        if approve:
            yes_votes = self.storage.get(f"{prefix}:vote_yes", U128(0))
            self.storage.set(f"{prefix}:vote_yes", yes_votes + pledge_weight)
        else:
            no_votes = self.storage.get(f"{prefix}:vote_no", U128(0))
            self.storage.set(f"{prefix}:vote_no", no_votes + pledge_weight)

        self.env.emit_event("milestone_vote_cast", {
            "campaign_id": campaign_id,
            "backer": backer,
            "approve": approve,
            "weight": pledge_weight,
        })

    @external
    def resolve_milestone_vote(self, caller: Address, campaign_id: Symbol):
        """Finalize the milestone release vote. If approved, releases milestone fraction to creator."""
        caller.require_auth()
        self._require_initialized()

        prefix = f"campaign:{campaign_id}"
        if not self.storage.get(f"{prefix}:vote_active", False):
            raise ContractError.NO_VOTE_ACTIVE

        current_ledger = self.env.ledger().sequence()
        vote_end = self.storage.get(f"{prefix}:vote_end")
        if current_ledger <= vote_end:
            raise ContractError.VOTE_PERIOD_ACTIVE

        yes_votes = self.storage.get(f"{prefix}:vote_yes", U128(0))
        no_votes = self.storage.get(f"{prefix}:vote_no", U128(0))

        # Decision rule: approved if yes > no
        approved = yes_votes > no_votes

        current_milestone = self.storage.get(f"{prefix}:current_milestone")
        self.storage.set(f"{prefix}:vote_active", False)

        creator = self.storage.get(f"{prefix}:creator")
        asset_token = self.storage.get("asset_token")

        if approved:
            # Calculate release amount: total_pledged * milestone_bps / 10000
            total_pledged = self.storage.get(f"{prefix}:total_pledged")
            milestone_bps = self.storage.get(f"{prefix}:milestone:{current_milestone}:bps")

            release_amount = (total_pledged * U128(milestone_bps)) // U128(10000)
            unreleased = self.storage.get(f"{prefix}:unreleased_pool")

            if release_amount > unreleased:
                release_amount = unreleased

            self.storage.set(f"{prefix}:unreleased_pool", unreleased - release_amount)
            self.storage.set(f"{prefix}:current_milestone", current_milestone + 1)

            # Transfer payout to creator
            self.env.transfer(asset_token, self.env.current_contract(), creator, release_amount)

            self.env.emit_event("milestone_resolved_approved", {
                "campaign_id": campaign_id,
                "milestone_index": current_milestone,
                "release_amount": release_amount,
            })
        else:
            # If rejected, campaign enters REFUNDING state where backers can withdraw remaining unreleased capital
            self.storage.set(f"{prefix}:state", CampaignState.REFUNDING)
            self.env.emit_event("milestone_resolved_rejected", {
                "campaign_id": campaign_id,
                "milestone_index": current_milestone,
            })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_campaign(self, campaign_id: Symbol) -> Map:
        """Get summary parameters and state of a campaign."""
        prefix = f"campaign:{campaign_id}"
        if not self.storage.get(f"{prefix}:exists", False):
            raise ContractError.CAMPAIGN_NOT_FOUND

        return {
            "creator": self.storage.get(f"{prefix}:creator"),
            "target": self.storage.get(f"{prefix}:target"),
            "deadline": self.storage.get(f"{prefix}:deadline"),
            "state": self.storage.get(f"{prefix}:state"),
            "total_pledged": self.storage.get(f"{prefix}:total_pledged"),
            "unreleased_pool": self.storage.get(f"{prefix}:unreleased_pool"),
            "current_milestone": self.storage.get(f"{prefix}:current_milestone"),
            "vote_active": self.storage.get(f"{prefix}:vote_active", False),
        }

    @view
    def get_backer_pledge(self, campaign_id: Symbol, backer: Address) -> U128:
        """Retrieve total tokens pledged by a backer."""
        return self.storage.get(f"pledge:{campaign_id}:{backer}", U128(0))

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
