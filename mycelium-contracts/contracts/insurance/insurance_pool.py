"""
Insurance Pool — Coverage pool with premium collection and claim management.

Mycelium Smart Contract for Stellar
Provides underwriter deposits for liquidity, policy purchase with premium
calculation, claim submission/assessment/payout, reserve ratio enforcement,
and policy renewal. Handles pool insolvency, simultaneous claims, expired
policy claims, and underwriter withdrawal during active claims.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    INSUFFICIENT_BALANCE = 4
    POOL_INSOLVENCY = 5
    POLICY_NOT_FOUND = 6
    POLICY_EXPIRED = 7
    POLICY_STILL_ACTIVE = 8
    CLAIM_NOT_FOUND = 9
    CLAIM_ALREADY_ASSESSED = 10
    CLAIM_ON_EXPIRED_POLICY = 11
    INVALID_RISK_TIER = 12
    INVALID_DURATION = 13
    INVALID_COVERAGE_AMOUNT = 14
    RESERVE_RATIO_VIOLATED = 15
    UNDERWRITER_NOT_FOUND = 16
    WITHDRAWAL_BLOCKED_ACTIVE_CLAIMS = 17
    DUPLICATE_VOTE = 18
    VOTING_PERIOD_ENDED = 19
    PAYOUT_EXCEEDS_POOL = 20
    CLAIM_NOT_APPROVED = 21
    ALREADY_CLAIMED = 22
    INSUFFICIENT_DEPOSIT = 23
    POLICY_ALREADY_RENEWED = 24
    MAX_CLAIMS_EXCEEDED = 25


class RiskTier:
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class ClaimStatus:
    SUBMITTED = 0
    UNDER_REVIEW = 1
    APPROVED = 2
    REJECTED = 3
    PAID_OUT = 4


class PolicyStatus:
    ACTIVE = 0
    EXPIRED = 1
    CLAIMED = 2
    CANCELLED = 3


@contract
class InsurancePool:
    """
    Decentralized insurance pool contract with full lifecycle management.

    Underwriters deposit funds to provide coverage liquidity.
    Policyholders purchase policies with risk-adjusted premiums.
    Claims are submitted with evidence and voted on by underwriters.
    Approved claims trigger payouts from the pool, subject to reserve constraints.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        coverage_token: Address,
        min_reserve_ratio_bps: U64,
        voting_period_ledgers: U64,
        min_underwriter_deposit: U128,
        max_coverage_per_policy: U128,
    ):
        """Initialize the insurance pool with base parameters."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if min_reserve_ratio_bps == 0 or min_reserve_ratio_bps > 10000:
            raise ContractError.RESERVE_RATIO_VIOLATED
        if voting_period_ledgers == 0:
            raise ContractError.INVALID_DURATION

        self.storage.set("admin", admin)
        self.storage.set("coverage_token", coverage_token)
        self.storage.set("min_reserve_ratio_bps", min_reserve_ratio_bps)
        self.storage.set("voting_period_ledgers", voting_period_ledgers)
        self.storage.set("min_underwriter_deposit", min_underwriter_deposit)
        self.storage.set("max_coverage_per_policy", max_coverage_per_policy)
        self.storage.set("total_pool_balance", U128(0))
        self.storage.set("total_active_coverage", U128(0))
        self.storage.set("total_premiums_collected", U128(0))
        self.storage.set("total_claims_paid", U128(0))
        self.storage.set("next_policy_id", U64(1))
        self.storage.set("next_claim_id", U64(1))
        self.storage.set("active_claim_count", U64(0))
        self.storage.set("underwriter_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("pool_initialized", {
            "admin": admin,
            "coverage_token": coverage_token,
            "min_reserve_ratio_bps": min_reserve_ratio_bps,
        })

    @external
    def deposit_underwriter(self, underwriter: Address, amount: U128):
        """Underwriter deposits funds into the coverage pool."""
        underwriter.require_auth()
        self._require_initialized()

        min_deposit = self.storage.get("min_underwriter_deposit")
        existing = self.storage.get(f"underwriter:{underwriter}:deposit", U128(0))

        if existing == 0 and amount < min_deposit:
            raise ContractError.INSUFFICIENT_DEPOSIT

        coverage_token = self.storage.get("coverage_token")
        self.env.transfer(coverage_token, underwriter, self.env.current_contract(), amount)

        new_balance = existing + amount
        self.storage.set(f"underwriter:{underwriter}:deposit", new_balance)
        self.storage.set(f"underwriter:{underwriter}:join_ledger", self.env.ledger().sequence())

        if existing == 0:
            count = self.storage.get("underwriter_count", U64(0))
            self.storage.set("underwriter_count", count + 1)
            self.storage.set(f"underwriter:{underwriter}:active", True)

        total = self.storage.get("total_pool_balance", U128(0))
        self.storage.set("total_pool_balance", total + amount)

        self.env.emit_event("underwriter_deposited", {
            "underwriter": underwriter,
            "amount": amount,
            "total_deposit": new_balance,
        })

    @external
    def withdraw_underwriter(self, underwriter: Address, amount: U128):
        """Underwriter withdraws funds from pool. Blocked if active claims exist."""
        underwriter.require_auth()
        self._require_initialized()

        deposit = self.storage.get(f"underwriter:{underwriter}:deposit", U128(0))
        if deposit == 0:
            raise ContractError.UNDERWRITER_NOT_FOUND

        active_claims = self.storage.get("active_claim_count", U64(0))
        if active_claims > 0:
            raise ContractError.WITHDRAWAL_BLOCKED_ACTIVE_CLAIMS

        if amount > deposit:
            raise ContractError.INSUFFICIENT_BALANCE

        remaining = deposit - amount
        total_pool = self.storage.get("total_pool_balance", U128(0))
        new_pool = total_pool - amount
        total_active_coverage = self.storage.get("total_active_coverage", U128(0))

        if total_active_coverage > 0:
            min_ratio = self.storage.get("min_reserve_ratio_bps")
            current_ratio_bps = (new_pool * 10000) // total_active_coverage
            if current_ratio_bps < min_ratio:
                raise ContractError.RESERVE_RATIO_VIOLATED

        self.storage.set(f"underwriter:{underwriter}:deposit", remaining)
        self.storage.set("total_pool_balance", new_pool)

        if remaining == 0:
            self.storage.set(f"underwriter:{underwriter}:active", False)
            count = self.storage.get("underwriter_count", U64(0))
            self.storage.set("underwriter_count", count - 1)

        coverage_token = self.storage.get("coverage_token")
        self.env.transfer(coverage_token, self.env.current_contract(), underwriter, amount)

        self.env.emit_event("underwriter_withdrew", {
            "underwriter": underwriter,
            "amount": amount,
            "remaining": remaining,
        })

    @external
    def purchase_policy(
        self,
        buyer: Address,
        coverage_amount: U128,
        duration_ledgers: U64,
        risk_tier: U64,
    ) -> U64:
        """Purchase an insurance policy. Premium is calculated based on coverage, duration, risk."""
        buyer.require_auth()
        self._require_initialized()

        if coverage_amount == 0:
            raise ContractError.INVALID_COVERAGE_AMOUNT
        max_cov = self.storage.get("max_coverage_per_policy")
        if coverage_amount > max_cov:
            raise ContractError.INVALID_COVERAGE_AMOUNT
        if duration_ledgers == 0:
            raise ContractError.INVALID_DURATION
        if risk_tier < RiskTier.LOW or risk_tier > RiskTier.CRITICAL:
            raise ContractError.INVALID_RISK_TIER

        total_pool = self.storage.get("total_pool_balance", U128(0))
        total_active = self.storage.get("total_active_coverage", U128(0))
        new_active = total_active + coverage_amount
        min_ratio = self.storage.get("min_reserve_ratio_bps")

        if total_pool > 0:
            ratio_bps = (total_pool * 10000) // new_active
            if ratio_bps < min_ratio:
                raise ContractError.POOL_INSOLVENCY

        premium = self._calculate_premium(coverage_amount, duration_ledgers, risk_tier)

        coverage_token = self.storage.get("coverage_token")
        self.env.transfer(coverage_token, buyer, self.env.current_contract(), premium)

        policy_id = self.storage.get("next_policy_id")
        current_ledger = self.env.ledger().sequence()
        expiry_ledger = current_ledger + duration_ledgers

        self.storage.set(f"policy:{policy_id}:holder", buyer)
        self.storage.set(f"policy:{policy_id}:coverage", coverage_amount)
        self.storage.set(f"policy:{policy_id}:premium", premium)
        self.storage.set(f"policy:{policy_id}:start_ledger", current_ledger)
        self.storage.set(f"policy:{policy_id}:expiry_ledger", expiry_ledger)
        self.storage.set(f"policy:{policy_id}:risk_tier", risk_tier)
        self.storage.set(f"policy:{policy_id}:status", PolicyStatus.ACTIVE)
        self.storage.set(f"policy:{policy_id}:claimed", False)
        self.storage.set(f"policy:{policy_id}:renewed", False)

        self.storage.set("next_policy_id", policy_id + 1)
        self.storage.set("total_active_coverage", new_active)
        total_premiums = self.storage.get("total_premiums_collected", U128(0))
        self.storage.set("total_premiums_collected", total_premiums + premium)
        self.storage.set("total_pool_balance", total_pool + premium)

        self.env.emit_event("policy_purchased", {
            "policy_id": policy_id,
            "holder": buyer,
            "coverage": coverage_amount,
            "premium": premium,
            "expiry_ledger": expiry_ledger,
            "risk_tier": risk_tier,
        })

        return policy_id

    @external
    def submit_claim(
        self,
        claimant: Address,
        policy_id: U64,
        claim_amount: U128,
        evidence_hash: Bytes,
    ) -> U64:
        """Submit a claim against an active policy with supporting evidence."""
        claimant.require_auth()
        self._require_initialized()

        holder = self.storage.get(f"policy:{policy_id}:holder", None)
        if holder is None:
            raise ContractError.POLICY_NOT_FOUND
        if holder != claimant:
            raise ContractError.UNAUTHORIZED

        status = self.storage.get(f"policy:{policy_id}:status")
        if status != PolicyStatus.ACTIVE:
            raise ContractError.POLICY_EXPIRED

        current_ledger = self.env.ledger().sequence()
        expiry = self.storage.get(f"policy:{policy_id}:expiry_ledger")
        if current_ledger > expiry:
            self.storage.set(f"policy:{policy_id}:status", PolicyStatus.EXPIRED)
            raise ContractError.CLAIM_ON_EXPIRED_POLICY

        if self.storage.get(f"policy:{policy_id}:claimed", False):
            raise ContractError.ALREADY_CLAIMED

        coverage = self.storage.get(f"policy:{policy_id}:coverage")
        if claim_amount > coverage:
            claim_amount = coverage

        claim_id = self.storage.get("next_claim_id")
        voting_period = self.storage.get("voting_period_ledgers")

        self.storage.set(f"claim:{claim_id}:policy_id", policy_id)
        self.storage.set(f"claim:{claim_id}:claimant", claimant)
        self.storage.set(f"claim:{claim_id}:amount", claim_amount)
        self.storage.set(f"claim:{claim_id}:evidence_hash", evidence_hash)
        self.storage.set(f"claim:{claim_id}:status", ClaimStatus.SUBMITTED)
        self.storage.set(f"claim:{claim_id}:submit_ledger", current_ledger)
        self.storage.set(f"claim:{claim_id}:vote_end_ledger", current_ledger + voting_period)
        self.storage.set(f"claim:{claim_id}:approve_votes", U128(0))
        self.storage.set(f"claim:{claim_id}:reject_votes", U128(0))
        self.storage.set(f"claim:{claim_id}:total_voters", U64(0))

        self.storage.set("next_claim_id", claim_id + 1)
        active = self.storage.get("active_claim_count", U64(0))
        self.storage.set("active_claim_count", active + 1)

        self.env.emit_event("claim_submitted", {
            "claim_id": claim_id,
            "policy_id": policy_id,
            "claimant": claimant,
            "amount": claim_amount,
            "evidence_hash": evidence_hash,
        })

        return claim_id

    @external
    def vote_on_claim(self, underwriter: Address, claim_id: U64, approve: Bool):
        """Underwriter votes to approve or reject a claim. Vote weight = deposit."""
        underwriter.require_auth()
        self._require_initialized()

        deposit = self.storage.get(f"underwriter:{underwriter}:deposit", U128(0))
        if deposit == 0:
            raise ContractError.UNDERWRITER_NOT_FOUND

        claim_status = self.storage.get(f"claim:{claim_id}:status", None)
        if claim_status is None:
            raise ContractError.CLAIM_NOT_FOUND
        if claim_status != ClaimStatus.SUBMITTED:
            raise ContractError.CLAIM_ALREADY_ASSESSED

        current_ledger = self.env.ledger().sequence()
        vote_end = self.storage.get(f"claim:{claim_id}:vote_end_ledger")
        if current_ledger > vote_end:
            raise ContractError.VOTING_PERIOD_ENDED

        if self.storage.get(f"claim:{claim_id}:voter:{underwriter}", False):
            raise ContractError.DUPLICATE_VOTE

        self.storage.set(f"claim:{claim_id}:voter:{underwriter}", True)

        if approve:
            current_approve = self.storage.get(f"claim:{claim_id}:approve_votes", U128(0))
            self.storage.set(f"claim:{claim_id}:approve_votes", current_approve + deposit)
        else:
            current_reject = self.storage.get(f"claim:{claim_id}:reject_votes", U128(0))
            self.storage.set(f"claim:{claim_id}:reject_votes", current_reject + deposit)

        voter_count = self.storage.get(f"claim:{claim_id}:total_voters", U64(0))
        self.storage.set(f"claim:{claim_id}:total_voters", voter_count + 1)

        self.storage.set(f"claim:{claim_id}:status", ClaimStatus.UNDER_REVIEW)

        self.env.emit_event("claim_vote_cast", {
            "claim_id": claim_id,
            "underwriter": underwriter,
            "approve": approve,
            "weight": deposit,
        })

    @external
    def finalize_claim(self, caller: Address, claim_id: U64):
        """Finalize voting on a claim once the voting period has ended."""
        caller.require_auth()
        self._require_initialized()

        claim_status = self.storage.get(f"claim:{claim_id}:status", None)
        if claim_status is None:
            raise ContractError.CLAIM_NOT_FOUND
        if claim_status not in (ClaimStatus.SUBMITTED, ClaimStatus.UNDER_REVIEW):
            raise ContractError.CLAIM_ALREADY_ASSESSED

        current_ledger = self.env.ledger().sequence()
        vote_end = self.storage.get(f"claim:{claim_id}:vote_end_ledger")
        if current_ledger <= vote_end:
            raise ContractError.VOTING_PERIOD_ENDED  # reused: voting not ended yet

        approve_votes = self.storage.get(f"claim:{claim_id}:approve_votes", U128(0))
        reject_votes = self.storage.get(f"claim:{claim_id}:reject_votes", U128(0))

        if approve_votes > reject_votes:
            self.storage.set(f"claim:{claim_id}:status", ClaimStatus.APPROVED)
            policy_id = self.storage.get(f"claim:{claim_id}:policy_id")
            self.storage.set(f"policy:{policy_id}:claimed", True)
            self.env.emit_event("claim_approved", {"claim_id": claim_id})
        else:
            self.storage.set(f"claim:{claim_id}:status", ClaimStatus.REJECTED)
            active = self.storage.get("active_claim_count", U64(0))
            if active > 0:
                self.storage.set("active_claim_count", active - 1)
            self.env.emit_event("claim_rejected", {"claim_id": claim_id})

    @external
    def payout_claim(self, caller: Address, claim_id: U64):
        """Execute payout for an approved claim."""
        caller.require_auth()
        self._require_initialized()

        status = self.storage.get(f"claim:{claim_id}:status", None)
        if status is None:
            raise ContractError.CLAIM_NOT_FOUND
        if status != ClaimStatus.APPROVED:
            raise ContractError.CLAIM_NOT_APPROVED

        amount = self.storage.get(f"claim:{claim_id}:amount")
        claimant = self.storage.get(f"claim:{claim_id}:claimant")
        policy_id = self.storage.get(f"claim:{claim_id}:policy_id")

        total_pool = self.storage.get("total_pool_balance", U128(0))
        if amount > total_pool:
            amount = total_pool
            if amount == 0:
                raise ContractError.POOL_INSOLVENCY

        coverage_token = self.storage.get("coverage_token")
        self.env.transfer(coverage_token, self.env.current_contract(), claimant, amount)

        self.storage.set(f"claim:{claim_id}:status", ClaimStatus.PAID_OUT)
        self.storage.set(f"claim:{claim_id}:payout_amount", amount)
        self.storage.set("total_pool_balance", total_pool - amount)

        coverage = self.storage.get(f"policy:{policy_id}:coverage")
        total_active = self.storage.get("total_active_coverage", U128(0))
        if total_active >= coverage:
            self.storage.set("total_active_coverage", total_active - coverage)
        else:
            self.storage.set("total_active_coverage", U128(0))

        self.storage.set(f"policy:{policy_id}:status", PolicyStatus.CLAIMED)

        total_paid = self.storage.get("total_claims_paid", U128(0))
        self.storage.set("total_claims_paid", total_paid + amount)

        active = self.storage.get("active_claim_count", U64(0))
        if active > 0:
            self.storage.set("active_claim_count", active - 1)

        self.env.emit_event("claim_paid_out", {
            "claim_id": claim_id,
            "policy_id": policy_id,
            "claimant": claimant,
            "amount": amount,
        })

    @external
    def renew_policy(self, holder: Address, policy_id: U64, new_duration_ledgers: U64) -> U64:
        """Renew an active or recently expired policy with a new duration."""
        holder.require_auth()
        self._require_initialized()

        stored_holder = self.storage.get(f"policy:{policy_id}:holder", None)
        if stored_holder is None:
            raise ContractError.POLICY_NOT_FOUND
        if stored_holder != holder:
            raise ContractError.UNAUTHORIZED
        if self.storage.get(f"policy:{policy_id}:renewed", False):
            raise ContractError.POLICY_ALREADY_RENEWED
        if self.storage.get(f"policy:{policy_id}:claimed", False):
            raise ContractError.ALREADY_CLAIMED

        coverage = self.storage.get(f"policy:{policy_id}:coverage")
        risk_tier = self.storage.get(f"policy:{policy_id}:risk_tier")

        self.storage.set(f"policy:{policy_id}:renewed", True)

        old_status = self.storage.get(f"policy:{policy_id}:status")
        if old_status == PolicyStatus.ACTIVE:
            total_active = self.storage.get("total_active_coverage", U128(0))
            if total_active >= coverage:
                self.storage.set("total_active_coverage", total_active - coverage)
        self.storage.set(f"policy:{policy_id}:status", PolicyStatus.EXPIRED)

        new_policy_id = self.purchase_policy(holder, coverage, new_duration_ledgers, risk_tier)

        self.env.emit_event("policy_renewed", {
            "old_policy_id": policy_id,
            "new_policy_id": new_policy_id,
            "holder": holder,
        })

        return new_policy_id

    @external
    def expire_policy(self, caller: Address, policy_id: U64):
        """Mark a policy as expired if past its expiry ledger."""
        caller.require_auth()
        self._require_initialized()

        status = self.storage.get(f"policy:{policy_id}:status", None)
        if status is None:
            raise ContractError.POLICY_NOT_FOUND
        if status != PolicyStatus.ACTIVE:
            raise ContractError.POLICY_EXPIRED

        current_ledger = self.env.ledger().sequence()
        expiry = self.storage.get(f"policy:{policy_id}:expiry_ledger")
        if current_ledger <= expiry:
            raise ContractError.POLICY_STILL_ACTIVE

        self.storage.set(f"policy:{policy_id}:status", PolicyStatus.EXPIRED)
        coverage = self.storage.get(f"policy:{policy_id}:coverage")
        total_active = self.storage.get("total_active_coverage", U128(0))
        if total_active >= coverage:
            self.storage.set("total_active_coverage", total_active - coverage)
        else:
            self.storage.set("total_active_coverage", U128(0))

        self.env.emit_event("policy_expired", {
            "policy_id": policy_id,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_policy(self, policy_id: U64) -> Map:
        """Return full policy details."""
        holder = self.storage.get(f"policy:{policy_id}:holder", None)
        if holder is None:
            raise ContractError.POLICY_NOT_FOUND
        return {
            "policy_id": policy_id,
            "holder": holder,
            "coverage": self.storage.get(f"policy:{policy_id}:coverage"),
            "premium": self.storage.get(f"policy:{policy_id}:premium"),
            "start_ledger": self.storage.get(f"policy:{policy_id}:start_ledger"),
            "expiry_ledger": self.storage.get(f"policy:{policy_id}:expiry_ledger"),
            "risk_tier": self.storage.get(f"policy:{policy_id}:risk_tier"),
            "status": self.storage.get(f"policy:{policy_id}:status"),
            "claimed": self.storage.get(f"policy:{policy_id}:claimed"),
        }

    @view
    def get_claim(self, claim_id: U64) -> Map:
        """Return full claim details."""
        claimant = self.storage.get(f"claim:{claim_id}:claimant", None)
        if claimant is None:
            raise ContractError.CLAIM_NOT_FOUND
        return {
            "claim_id": claim_id,
            "policy_id": self.storage.get(f"claim:{claim_id}:policy_id"),
            "claimant": claimant,
            "amount": self.storage.get(f"claim:{claim_id}:amount"),
            "status": self.storage.get(f"claim:{claim_id}:status"),
            "approve_votes": self.storage.get(f"claim:{claim_id}:approve_votes"),
            "reject_votes": self.storage.get(f"claim:{claim_id}:reject_votes"),
        }

    @view
    def get_pool_stats(self) -> Map:
        """Return pool-level statistics."""
        return {
            "total_pool_balance": self.storage.get("total_pool_balance", U128(0)),
            "total_active_coverage": self.storage.get("total_active_coverage", U128(0)),
            "total_premiums_collected": self.storage.get("total_premiums_collected", U128(0)),
            "total_claims_paid": self.storage.get("total_claims_paid", U128(0)),
            "active_claim_count": self.storage.get("active_claim_count", U64(0)),
            "underwriter_count": self.storage.get("underwriter_count", U64(0)),
        }

    @view
    def get_underwriter_info(self, underwriter: Address) -> Map:
        """Return underwriter deposit and status."""
        deposit = self.storage.get(f"underwriter:{underwriter}:deposit", U128(0))
        return {
            "underwriter": underwriter,
            "deposit": deposit,
            "active": self.storage.get(f"underwriter:{underwriter}:active", False),
            "join_ledger": self.storage.get(f"underwriter:{underwriter}:join_ledger", U64(0)),
        }

    @view
    def get_reserve_ratio_bps(self) -> U64:
        """Return current reserve ratio in basis points."""
        total_pool = self.storage.get("total_pool_balance", U128(0))
        total_active = self.storage.get("total_active_coverage", U128(0))
        if total_active == 0:
            return U64(10000)
        return U64((total_pool * 10000) // total_active)

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        """Guard: contract must be initialized."""
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        """Guard: caller must be the admin."""
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _calculate_premium(
        self, coverage_amount: U128, duration_ledgers: U64, risk_tier: U64
    ) -> U128:
        """
        Calculate premium based on coverage amount, duration, and risk tier.

        Base rate: 2% of coverage per standard period (100,000 ledgers).
        Risk multipliers: LOW=1x, MEDIUM=1.5x, HIGH=2.5x, CRITICAL=4x.
        Duration scaling is linear.
        """
        base_rate_bps = U128(200)  # 2%
        standard_period = U128(100000)

        risk_multiplier_bps = U128(10000)
        if risk_tier == RiskTier.LOW:
            risk_multiplier_bps = U128(10000)
        elif risk_tier == RiskTier.MEDIUM:
            risk_multiplier_bps = U128(15000)
        elif risk_tier == RiskTier.HIGH:
            risk_multiplier_bps = U128(25000)
        elif risk_tier == RiskTier.CRITICAL:
            risk_multiplier_bps = U128(40000)

        premium = coverage_amount * base_rate_bps * U128(duration_ledgers) * risk_multiplier_bps
        premium = premium // (U128(10000) * standard_period * U128(10000))

        if premium == 0:
            premium = U128(1)

        return premium
