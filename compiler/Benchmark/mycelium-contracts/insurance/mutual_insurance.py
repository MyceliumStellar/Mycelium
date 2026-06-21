"""
Mutual Insurance — Peer-to-peer mutual insurance pool with member voting and dynamic premiums.

Mycelium Smart Contract for Stellar
Underwriters and policyholders are members of a P2P mutual pool.
Members deposit capital to earn premium shares, purchase coverage using
proportional premium recalculations, vote on claims based on pool share weight,
and retrieve exit allocations when leaving the pool.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    INVALID_AMOUNT = 4
    INSUFFICIENT_SHARES = 5
    ACTIVE_CLAIMS_EXIST = 6
    RESERVE_RATIO_VIOLATED = 7
    MEMBER_NOT_FOUND = 8
    POLICY_NOT_FOUND = 9
    POLICY_EXPIRED = 10
    CLAIM_NOT_FOUND = 11
    CLAIM_ALREADY_ASSESSED = 12
    VOTING_PERIOD_ACTIVE = 13
    VOTING_PERIOD_EXPIRED = 14
    DUPLICATE_VOTE = 15
    NOT_A_MEMBER = 16
    ALREADY_CLAIMED = 17
    CLAIM_NOT_APPROVED = 18
    TRANSFER_FAILED = 19
    INVALID_DURATION = 20


class ClaimStatus:
    SUBMITTED = 0
    APPROVED = 1
    REJECTED = 2
    PAID_OUT = 3


@contract
class MutualInsurance:
    """
    Mutual Insurance Pool contract where P2P members co-underwrite each other,
    recalculate premiums dynamically, and vote on claims.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        asset_token: Address,
        min_reserve_ratio_bps: U64,
        voting_period_ledgers: U64,
    ):
        """Initialize the mutual pool with core risk parameters."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if min_reserve_ratio_bps < 10000:  # Must be at least 100% (10000 bps)
            raise ContractError.RESERVE_RATIO_VIOLATED
        if voting_period_ledgers == 0:
            raise ContractError.INVALID_DURATION

        self.storage.set("admin", admin)
        self.storage.set("asset_token", asset_token)
        self.storage.set("min_reserve_ratio_bps", min_reserve_ratio_bps)
        self.storage.set("voting_period_ledgers", voting_period_ledgers)

        self.storage.set("total_pool_assets", U128(0))
        self.storage.set("total_shares", U128(0))
        self.storage.set("total_active_coverage", U128(0))
        self.storage.set("active_claims_count", U64(0))
        self.storage.set("next_policy_id", U64(1))
        self.storage.set("next_claim_id", U64(1))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "asset_token": asset_token,
            "min_reserve_ratio": min_reserve_ratio_bps,
        })

    @external
    def join_mutual(self, member: Address, deposit_amount: U128) -> U128:
        """
        Join the mutual pool by depositing capital.
        Mints pool shares to the depositor based on current share price.
        """
        member.require_auth()
        self._require_initialized()

        if deposit_amount == 0:
            raise ContractError.INVALID_AMOUNT

        total_assets = self.storage.get("total_pool_assets", U128(0))
        total_shares = self.storage.get("total_shares", U128(0))

        # Mint shares proportionally
        shares_to_mint = U128(0)
        if total_shares == 0:
            shares_to_mint = deposit_amount
        else:
            shares_to_mint = (deposit_amount * total_shares) // total_assets

        # Transfer asset tokens
        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, member, self.env.current_contract(), deposit_amount)

        # Update member state
        member_shares = self.storage.get(f"shares:{member}", U128(0))
        self.storage.set(f"shares:{member}", member_shares + shares_to_mint)
        self.storage.set(f"total_shares", total_shares + shares_to_mint)
        self.storage.set("total_pool_assets", total_assets + deposit_amount)

        self.env.emit_event("joined", {
            "member": member,
            "deposit_amount": deposit_amount,
            "shares_minted": shares_to_mint,
        })

        return shares_to_mint

    @external
    def buy_policy(self, member: Address, coverage_amount: U128, duration_ledgers: U64) -> U64:
        """
        Purchase a policy. Premium is recalculate dynamically based on pool exposure.
        """
        member.require_auth()
        self._require_initialized()

        # Buyer must have joined the mutual pool (even with 0 shares, but let's require membership)
        if self.storage.get(f"shares:{member}", U128(0)) == 0:
            raise ContractError.NOT_A_MEMBER

        if coverage_amount == 0:
            raise ContractError.INVALID_AMOUNT
        if duration_ledgers == 0:
            raise ContractError.INVALID_DURATION

        total_assets = self.storage.get("total_pool_assets", U128(0))
        total_active = self.storage.get("total_active_coverage", U128(0))

        # Safety check: enforce minimum reserve ratio
        new_active = total_active + coverage_amount
        min_reserve = self.storage.get("min_reserve_ratio_bps")

        if total_assets > 0:
            current_reserve_bps = (total_assets * 10000) // new_active
            if current_reserve_bps < min_reserve:
                raise ContractError.RESERVE_RATIO_VIOLATED
        else:
            raise ContractError.RESERVE_RATIO_VIOLATED

        # Recalculate premium dynamically based on pool risk
        premium = self._calculate_premium(coverage_amount, duration_ledgers, total_active, total_assets)

        # Transfer premium to the contract
        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, member, self.env.current_contract(), premium)

        # Mint shares for the premium paid (premium increases the total pool value, minting shares keeps pricing fair)
        # Note: In standard mutuals, premium adds value to the fund, but the purchaser may or may not receive new shares.
        # Let's say premium adds to the fund value without minting new shares, thus increasing the value of existing shares.
        # This rewards long-term capital providers.
        self.storage.set("total_pool_assets", total_assets + premium)

        policy_id = self.storage.get("next_policy_id")
        self.storage.set("next_policy_id", policy_id + 1)

        current_ledger = self.env.ledger().sequence()
        expiry_ledger = current_ledger + duration_ledgers

        self.storage.set(f"policy:{policy_id}:holder", member)
        self.storage.set(f"policy:{policy_id}:coverage", coverage_amount)
        self.storage.set(f"policy:{policy_id}:expiry", expiry_ledger)
        self.storage.set(f"policy:{policy_id}:claimed", False)

        self.storage.set("total_active_coverage", new_active)

        self.env.emit_event("policy_purchased", {
            "policy_id": policy_id,
            "holder": member,
            "coverage": coverage_amount,
            "premium": premium,
            "expiry": expiry_ledger,
        })

        return policy_id

    @external
    def submit_claim(self, claimant: Address, policy_id: U64, claim_amount: U128) -> U64:
        """Submit a claim for an active policy."""
        claimant.require_auth()
        self._require_initialized()

        holder = self.storage.get(f"policy:{policy_id}:holder", None)
        if holder is None:
            raise ContractError.POLICY_NOT_FOUND
        if holder != claimant:
            raise ContractError.UNAUTHORIZED

        # Check expiry
        expiry = self.storage.get(f"policy:{policy_id}:expiry")
        if self.env.ledger().sequence() > expiry:
            raise ContractError.POLICY_EXPIRED

        if self.storage.get(f"policy:{policy_id}:claimed", False):
            raise ContractError.ALREADY_CLAIMED

        coverage = self.storage.get(f"policy:{policy_id}:coverage")
        if claim_amount == 0 or claim_amount > coverage:
            raise ContractError.INVALID_AMOUNT

        claim_id = self.storage.get("next_claim_id")
        self.storage.set("next_claim_id", claim_id + 1)

        voting_period = self.storage.get("voting_period_ledgers")
        current_ledger = self.env.ledger().sequence()

        self.storage.set(f"claim:{claim_id}:policy_id", policy_id)
        self.storage.set(f"claim:{claim_id}:claimant", claimant)
        self.storage.set(f"claim:{claim_id}:amount", claim_amount)
        self.storage.set(f"claim:{claim_id}:status", ClaimStatus.SUBMITTED)
        self.storage.set(f"claim:{claim_id}:vote_end", current_ledger + voting_period)
        self.storage.set(f"claim:{claim_id}:yes_votes", U128(0))
        self.storage.set(f"claim:{claim_id}:no_votes", U128(0))

        active_claims = self.storage.get("active_claims_count", U64(0))
        self.storage.set("active_claims_count", active_claims + 1)

        self.env.emit_event("claim_submitted", {
            "claim_id": claim_id,
            "policy_id": policy_id,
            "claimant": claimant,
            "amount": claim_amount,
        })

        return claim_id

    @external
    def vote_on_claim(self, voter: Address, claim_id: U64, approve: Bool):
        """Vote on a claim. Voting weight is determined by member's shares."""
        voter.require_auth()
        self._require_initialized()

        shares = self.storage.get(f"shares:{voter}", U128(0))
        if shares == 0:
            raise ContractError.NOT_A_MEMBER

        status = self.storage.get(f"claim:{claim_id}:status", None)
        if status is None:
            raise ContractError.CLAIM_NOT_FOUND
        if status != ClaimStatus.SUBMITTED:
            raise ContractError.CLAIM_ALREADY_ASSESSED

        vote_end = self.storage.get(f"claim:{claim_id}:vote_end")
        if self.env.ledger().sequence() > vote_end:
            raise ContractError.VOTING_PERIOD_EXPIRED

        if self.storage.get(f"claim:{claim_id}:voted:{voter}", False):
            raise ContractError.DUPLICATE_VOTE

        self.storage.set(f"claim:{claim_id}:voted:{voter}", True)

        if approve:
            yes_votes = self.storage.get(f"claim:{claim_id}:yes_votes", U128(0))
            self.storage.set(f"claim:{claim_id}:yes_votes", yes_votes + shares)
        else:
            no_votes = self.storage.get(f"claim:{claim_id}:no_votes", U128(0))
            self.storage.set(f"claim:{claim_id}:no_votes", no_votes + shares)

        self.env.emit_event("claim_voted", {
            "claim_id": claim_id,
            "voter": voter,
            "approve": approve,
            "shares": shares,
        })

    @external
    def resolve_claim(self, caller: Address, claim_id: U64):
        """Resolve the claim after voting ends and execute payout if approved."""
        caller.require_auth()
        self._require_initialized()

        status = self.storage.get(f"claim:{claim_id}:status", None)
        if status is None:
            raise ContractError.CLAIM_NOT_FOUND
        if status != ClaimStatus.SUBMITTED:
            raise ContractError.CLAIM_ALREADY_ASSESSED

        vote_end = self.storage.get(f"claim:{claim_id}:vote_end")
        if self.env.ledger().sequence() <= vote_end:
            raise ContractError.VOTING_PERIOD_ACTIVE

        yes_votes = self.storage.get(f"claim:{claim_id}:yes_votes", U128(0))
        no_votes = self.storage.get(f"claim:{claim_id}:no_votes", U128(0))
        claimant = self.storage.get(f"claim:{claim_id}:claimant")
        amount = self.storage.get(f"claim:{claim_id}:amount")
        policy_id = self.storage.get(f"claim:{claim_id}:policy_id")

        active_claims = self.storage.get("active_claims_count", U64(0))
        if active_claims > 0:
            self.storage.set("active_claims_count", active_claims - 1)

        # Simple majority of votes cast resolves claim
        if yes_votes > no_votes:
            self.storage.set(f"claim:{claim_id}:status", ClaimStatus.APPROVED)
            self.storage.set(f"policy:{policy_id}:claimed", True)

            # Process payout
            total_assets = self.storage.get("total_pool_assets", U128(0))
            payout = amount
            if payout > total_assets:
                payout = total_assets  # Cap at pool assets

            self.storage.set("total_pool_assets", total_assets - payout)

            # Decrease active coverage
            cov = self.storage.get(f"policy:{policy_id}:coverage")
            total_active = self.storage.get("total_active_coverage", U128(0))
            if total_active >= cov:
                self.storage.set("total_active_coverage", total_active - cov)

            # Transfer payout
            asset_token = self.storage.get("asset_token")
            self.env.transfer(asset_token, self.env.current_contract(), claimant, payout)

            self.storage.set(f"claim:{claim_id}:status", ClaimStatus.PAID_OUT)
            self.env.emit_event("claim_resolved", {
                "claim_id": claim_id,
                "approved": True,
                "payout": payout,
            })
        else:
            self.storage.set(f"claim:{claim_id}:status", ClaimStatus.REJECTED)
            self.env.emit_event("claim_resolved", {
                "claim_id": claim_id,
                "approved": False,
                "payout": U128(0),
            })

    @external
    def exit_mutual(self, member: Address, shares_to_burn: U128) -> U128:
        """
        Exit the mutual pool by burning shares and retrieving proportional pool assets.
        Blocked if there are active claims or reserve ratio is violated.
        """
        member.require_auth()
        self._require_initialized()

        if shares_to_burn == 0:
            raise ContractError.INVALID_AMOUNT

        # Block exit if there are active claims to ensure members cannot run from liabilities
        active_claims = self.storage.get("active_claims_count", U64(0))
        if active_claims > 0:
            raise ContractError.ACTIVE_CLAIMS_EXIST

        member_shares = self.storage.get(f"shares:{member}", U128(0))
        if shares_to_burn > member_shares:
            raise ContractError.INSUFFICIENT_SHARES

        total_assets = self.storage.get("total_pool_assets", U128(0))
        total_shares = self.storage.get("total_shares", U128(0))

        # Calculate proportional payout allocation
        payout = (shares_to_burn * total_assets) // total_shares

        new_assets = total_assets - payout
        total_active = self.storage.get("total_active_coverage", U128(0))

        # Enforce reserve ratio check post-withdrawal if coverage remains
        if total_active > 0:
            min_reserve = self.storage.get("min_reserve_ratio_bps")
            new_reserve_bps = (new_assets * 10000) // total_active
            if new_reserve_bps < min_reserve:
                raise ContractError.RESERVE_RATIO_VIOLATED

        # Update states
        self.storage.set(f"shares:{member}", member_shares - shares_to_burn)
        self.storage.set("total_shares", total_shares - shares_to_burn)
        self.storage.set("total_pool_assets", new_assets)

        # Transfer tokens
        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, self.env.current_contract(), member, payout)

        self.env.emit_event("exited", {
            "member": member,
            "shares_burned": shares_to_burn,
            "payout_allocation": payout,
        })

        return payout

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_member_shares(self, member: Address) -> U128:
        """Get the shares of a member."""
        return self.storage.get(f"shares:{member}", U128(0))

    @view
    def get_pool_stats(self) -> Map:
        """Get mutual pool parameters and statistics."""
        return {
            "total_assets": self.storage.get("total_pool_assets", U128(0)),
            "total_shares": self.storage.get("total_shares", U128(0)),
            "total_active_coverage": self.storage.get("total_active_coverage", U128(0)),
            "active_claims_count": self.storage.get("active_claims_count", U64(0)),
        }

    @view
    def get_policy(self, policy_id: U64) -> Map:
        """Retrieve policy details."""
        holder = self.storage.get(f"policy:{policy_id}:holder", None)
        if holder is None:
            raise ContractError.POLICY_NOT_FOUND
        return {
            "policy_id": policy_id,
            "holder": holder,
            "coverage": self.storage.get(f"policy:{policy_id}:coverage"),
            "expiry": self.storage.get(f"policy:{policy_id}:expiry"),
            "claimed": self.storage.get(f"policy:{policy_id}:claimed"),
        }

    @view
    def get_claim(self, claim_id: U64) -> Map:
        """Retrieve claim details."""
        claimant = self.storage.get(f"claim:{claim_id}:claimant", None)
        if claimant is None:
            raise ContractError.CLAIM_NOT_FOUND
        return {
            "claim_id": claim_id,
            "policy_id": self.storage.get(f"claim:{claim_id}:policy_id"),
            "claimant": claimant,
            "amount": self.storage.get(f"claim:{claim_id}:amount"),
            "status": self.storage.get(f"claim:{claim_id}:status"),
            "vote_end": self.storage.get(f"claim:{claim_id}:vote_end"),
            "yes_votes": self.storage.get(f"claim:{claim_id}:yes_votes"),
            "no_votes": self.storage.get(f"claim:{claim_id}:no_votes"),
        }

    @view
    def quote_premium(self, coverage_amount: U128, duration_ledgers: U64) -> U128:
        """
        Calculate premium dynamically based on pool exposure.
        Enables external callers to preview premium costs.
        """
        total_assets = self.storage.get("total_pool_assets", U128(0))
        total_active = self.storage.get("total_active_coverage", U128(0))
        return self._calculate_premium(coverage_amount, duration_ledgers, total_active, total_assets)

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _calculate_premium(
        self, coverage_amount: U128, duration_ledgers: U64, total_active: U128, total_assets: U128
    ) -> U128:
        """
        Recalculate premium proportionally.
        Formula:
        Base premium: 1.5% per 100,000 ledgers.
        Risk multiplier increases proportionally to (total_active + coverage_amount) / total_assets.
        """
        base_rate_bps = U128(150)
        standard_period = U128(100000)

        # Risk multiplier defaults to 1x (10000 bps) if total assets is 0
        risk_multiplier_bps = U128(10000)
        if total_assets > 0:
            # Multiplier scales up if total active coverage is high compared to pool size
            # E.g. risk ratio = (total_active * 10000) // total_assets
            risk_ratio = (total_active * U128(10000)) // total_assets
            risk_multiplier_bps = U128(10000) + risk_ratio

        # Calculate final premium
        premium = coverage_amount * base_rate_bps * U128(duration_ledgers) * risk_multiplier_bps
        premium = premium // (U128(10000) * standard_period * U128(10000))

        if premium == 0:
            premium = U128(1)

        return premium
