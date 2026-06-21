"""
Smart Contract Cover — De-Fi protocol insurance with stake-weighted claim voting.

Mycelium Smart Contract for Stellar
Provides cover purchase for listed protocols, capital staking, risk weight
adjustment, claim submission with evidence, and stake-weighted community arbitration.
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
    PROTOCOL_NOT_FOUND = 5
    PROTOCOL_ALREADY_EXISTS = 6
    INSUFFICIENT_STAKE = 7
    INSUFFICIENT_CAPACITY = 8
    POLICY_NOT_FOUND = 9
    POLICY_EXPIRED = 10
    CLAIM_NOT_FOUND = 11
    CLAIM_EXPIRED = 12
    CLAIM_ALREADY_RESOLVED = 13
    VOTING_PERIOD_ACTIVE = 14
    VOTING_PERIOD_CLOSED = 15
    STAKER_ALREADY_VOTED = 16
    STAKE_LOCKED = 17
    CLAIM_EXCEEDS_COVERAGE = 18


@contract
class SmartContractCover:
    """
    Smart Contract Cover managing pools of capital per protocol, risk-weighted cover policies,
    and stake-weighted voting on claims.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        asset_token: Address,
        min_stake: U128,
        voting_duration_ledgers: U64,
        unstake_lock_duration: U64,
    ):
        """Initialize the smart contract cover system."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if min_stake == 0 or voting_duration_ledgers == 0 or unstake_lock_duration == 0:
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("admin", admin)
        self.storage.set("asset_token", asset_token)
        self.storage.set("min_stake", min_stake)
        self.storage.set("voting_duration", voting_duration_ledgers)
        self.storage.set("unstake_lock_duration", unstake_lock_duration)
        self.storage.set("policy_count", U64(0))
        self.storage.set("claim_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "asset_token": asset_token,
            "min_stake": min_stake,
        })

    @external
    def list_protocol(
        self,
        admin: Address,
        protocol_id: Symbol,
        risk_weight_bps: U64,
        max_capacity: U128,
    ):
        """List a protocol to enable buying cover policies."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        if risk_weight_bps == 0 or risk_weight_bps > 10000 or max_capacity == 0:
            raise ContractError.INVALID_PARAMETERS

        if self.storage.get(f"protocol:{protocol_id}:listed", False):
            raise ContractError.PROTOCOL_ALREADY_EXISTS

        self.storage.set(f"protocol:{protocol_id}:listed", True)
        self.storage.set(f"protocol:{protocol_id}:risk_weight", risk_weight_bps)
        self.storage.set(f"protocol:{protocol_id}:max_capacity", max_capacity)
        self.storage.set(f"protocol:{protocol_id}:total_staked", U128(0))
        self.storage.set(f"protocol:{protocol_id}:active_coverage", U128(0))

        self.env.emit_event("protocol_listed", {
            "protocol_id": protocol_id,
            "risk_weight": risk_weight_bps,
            "max_capacity": max_capacity,
        })

    @external
    def update_protocol_risk(
        self,
        admin: Address,
        protocol_id: Symbol,
        risk_weight_bps: U64,
        max_capacity: U128,
    ):
        """Update an existing protocol's parameters."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        if not self.storage.get(f"protocol:{protocol_id}:listed", False):
            raise ContractError.PROTOCOL_NOT_FOUND

        if risk_weight_bps == 0 or risk_weight_bps > 10000 or max_capacity == 0:
            raise ContractError.INVALID_PARAMETERS

        self.storage.set(f"protocol:{protocol_id}:risk_weight", risk_weight_bps)
        self.storage.set(f"protocol:{protocol_id}:max_capacity", max_capacity)

        self.env.emit_event("protocol_updated", {
            "protocol_id": protocol_id,
            "risk_weight": risk_weight_bps,
            "max_capacity": max_capacity,
        })

    @external
    def stake(self, staker: Address, protocol_id: Symbol, amount: U128):
        """Stake asset tokens in a protocol's capital pool to earn premiums."""
        staker.require_auth()
        self._require_initialized()

        if not self.storage.get(f"protocol:{protocol_id}:listed", False):
            raise ContractError.PROTOCOL_NOT_FOUND

        min_stake = self.storage.get("min_stake")
        if amount < min_stake:
            raise ContractError.INVALID_PARAMETERS

        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, staker, self.env.current_contract(), amount)

        # Update stake totals
        total_staked = self.storage.get(f"protocol:{protocol_id}:total_staked", U128(0))
        self.storage.set(f"protocol:{protocol_id}:total_staked", total_staked + amount)

        staker_stake = self.storage.get(f"staker:{staker}:{protocol_id}:amount", U128(0))
        self.storage.set(f"staker:{staker}:{protocol_id}:amount", staker_stake + amount)

        self.env.emit_event("capital_staked", {
            "staker": staker,
            "protocol_id": protocol_id,
            "amount": amount,
        })

    @external
    def request_unstake(self, staker: Address, protocol_id: Symbol):
        """Initiate unstaking with a safety lockup period."""
        staker.require_auth()
        self._require_initialized()

        staker_stake = self.storage.get(f"staker:{staker}:{protocol_id}:amount", U128(0))
        if staker_stake == 0:
            raise ContractError.INSUFFICIENT_STAKE

        current_ledger = self.env.ledger().sequence()
        lock_dur = self.storage.get("unstake_lock_duration")
        unlock_ledger = current_ledger + lock_dur

        self.storage.set(f"staker:{staker}:{protocol_id}:unlock_ledger", unlock_ledger)

        self.env.emit_event("unstake_requested", {
            "staker": staker,
            "protocol_id": protocol_id,
            "unlock_ledger": unlock_ledger,
        })

    @external
    def withdraw_unstaked(self, staker: Address, protocol_id: Symbol, amount: U128):
        """Withdraw capital once the unlock lockup has expired."""
        staker.require_auth()
        self._require_initialized()

        unlock_ledger = self.storage.get(f"staker:{staker}:{protocol_id}:unlock_ledger", U64(0))
        if unlock_ledger == 0:
            raise ContractError.STAKE_LOCKED

        current_ledger = self.env.ledger().sequence()
        if current_ledger < unlock_ledger:
            raise ContractError.STAKE_LOCKED

        staker_stake = self.storage.get(f"staker:{staker}:{protocol_id}:amount", U128(0))
        if amount > staker_stake:
            raise ContractError.INSUFFICIENT_STAKE

        # Check that we are not unstaking below what is locked by active coverage
        # Active capacity check: total staked must remain >= active_coverage // 2 (2x leverage ratio)
        active_cov = self.storage.get(f"protocol:{protocol_id}:active_coverage", U128(0))
        total_staked = self.storage.get(f"protocol:{protocol_id}:total_staked", U128(0))
        new_total_staked = total_staked - amount

        if new_total_staked * U128(2) < active_cov:
            raise ContractError.INSUFFICIENT_CAPACITY

        self.storage.set(f"staker:{staker}:{protocol_id}:amount", staker_stake - amount)
        self.storage.set(f"protocol:{protocol_id}:total_staked", new_total_staked)

        # Clear unlock request
        self.storage.set(f"staker:{staker}:{protocol_id}:unlock_ledger", U64(0))

        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, self.env.current_contract(), staker, amount)

        self.env.emit_event("capital_withdrawn", {
            "staker": staker,
            "protocol_id": protocol_id,
            "amount": amount,
        })

    @external
    def buy_cover(
        self,
        buyer: Address,
        protocol_id: Symbol,
        coverage_amount: U128,
        duration_ledgers: U64,
    ) -> U64:
        """Buy smart contract exploit cover for a protocol."""
        buyer.require_auth()
        self._require_initialized()

        if not self.storage.get(f"protocol:{protocol_id}:listed", False):
            raise ContractError.PROTOCOL_NOT_FOUND

        if coverage_amount == 0 or duration_ledgers == 0:
            raise ContractError.INVALID_PARAMETERS

        total_staked = self.storage.get(f"protocol:{protocol_id}:total_staked", U128(0))
        active_coverage = self.storage.get(f"protocol:{protocol_id}:active_coverage", U128(0))
        max_capacity = self.storage.get(f"protocol:{protocol_id}:max_capacity")

        # Capacity check: Active coverage cannot exceed max capacity or 2x the staked capital pool
        new_active_coverage = active_coverage + coverage_amount
        if new_active_coverage > max_capacity or new_active_coverage > total_staked * U128(2):
            raise ContractError.INSUFFICIENT_CAPACITY

        # Premium calculation: based on duration and risk weight
        # premium = (coverage * risk_weight_bps * duration) / 10,000 / 100,000 (100k ledgers is base unit)
        risk_weight = self.storage.get(f"protocol:{protocol_id}:risk_weight")
        premium = (coverage_amount * U128(risk_weight) * U128(duration_ledgers)) // U128(1000000000)
        if premium == 0:
            premium = U128(1)

        asset_token = self.storage.get("asset_token")
        # Collect premium and distribute it as pool reward (accumulates inside contract for stakers)
        self.env.transfer(asset_token, buyer, self.env.current_contract(), premium)

        # Distribute premium to protocol capital pool (adds to total staked)
        self.storage.set(f"protocol:{protocol_id}:total_staked", total_staked + premium)

        # Record policy
        policy_id = self.storage.get("policy_count") + 1
        self.storage.set("policy_count", policy_id)

        expiry_ledger = self.env.ledger().sequence() + duration_ledgers

        self.storage.set(f"policy:{policy_id}:holder", buyer)
        self.storage.set(f"policy:{policy_id}:protocol_id", protocol_id)
        self.storage.set(f"policy:{policy_id}:coverage", coverage_amount)
        self.storage.set(f"policy:{policy_id}:expiry", expiry_ledger)
        self.storage.set(f"policy:{policy_id}:claimed", U128(0))

        # Update active coverage
        self.storage.set(f"protocol:{protocol_id}:active_coverage", new_active_coverage)

        self.env.emit_event("cover_purchased", {
            "policy_id": policy_id,
            "buyer": buyer,
            "protocol_id": protocol_id,
            "coverage": coverage_amount,
            "premium": premium,
            "expiry": expiry_ledger,
        })

        return policy_id

    @external
    def submit_claim(
        self,
        policyholder: Address,
        policy_id: U64,
        evidence_hash: Bytes,
        claimed_amount: U128,
    ) -> U64:
        """Submit an exploit claim with cryptographic or tx hashes as evidence."""
        policyholder.require_auth()
        self._require_initialized()

        holder = self.storage.get(f"policy:{policy_id}:holder", None)
        if holder is None:
            raise ContractError.POLICY_NOT_FOUND

        if holder != policyholder:
            raise ContractError.UNAUTHORIZED

        expiry = self.storage.get(f"policy:{policy_id}:expiry")
        current_ledger = self.env.ledger().sequence()
        # Allow claiming up to 20,000 ledgers after policy expiry (claim reporting window)
        if current_ledger > expiry + U64(20000):
            raise ContractError.POLICY_EXPIRED

        coverage = self.storage.get(f"policy:{policy_id}:coverage")
        already_claimed = self.storage.get(f"policy:{policy_id}:claimed", U128(0))
        remaining_cov = coverage - already_claimed

        if claimed_amount == 0 or claimed_amount > remaining_cov:
            raise ContractError.CLAIM_EXCEEDS_COVERAGE

        # Create claim record
        claim_id = self.storage.get("claim_count") + 1
        self.storage.set("claim_count", claim_id)

        voting_duration = self.storage.get("voting_duration")
        voting_end = current_ledger + voting_duration

        self.storage.set(f"claim:{claim_id}:policy_id", policy_id)
        self.storage.set(f"claim:{claim_id}:amount", claimed_amount)
        self.storage.set(f"claim:{claim_id}:evidence", evidence_hash)
        self.storage.set(f"claim:{claim_id}:voting_end", voting_end)
        self.storage.set(f"claim:{claim_id}:resolved", False)
        self.storage.set(f"claim:{claim_id}:yes_votes", U128(0))
        self.storage.set(f"claim:{claim_id}:no_votes", U128(0))

        self.env.emit_event("claim_submitted", {
            "claim_id": claim_id,
            "policy_id": policy_id,
            "claimed_amount": claimed_amount,
            "evidence": evidence_hash,
            "voting_end": voting_end,
        })

        return claim_id

    @external
    def vote_on_claim(self, staker: Address, claim_id: U64, vote_yes: Bool):
        """Cast stake-weighted vote on a submitted claim validation."""
        staker.require_auth()
        self._require_initialized()

        # Check claim existence
        policy_id = self.storage.get(f"claim:{claim_id}:policy_id", None)
        if policy_id is None:
            raise ContractError.CLAIM_NOT_FOUND

        if self.storage.get(f"claim:{claim_id}:resolved", False):
            raise ContractError.CLAIM_ALREADY_RESOLVED

        current_ledger = self.env.ledger().sequence()
        voting_end = self.storage.get(f"claim:{claim_id}:voting_end")
        if current_ledger > voting_end:
            raise ContractError.VOTING_PERIOD_CLOSED

        if self.storage.get(f"claim:{claim_id}:voted:{staker}", False):
            raise ContractError.STAKER_ALREADY_VOTED

        protocol_id = self.storage.get(f"policy:{policy_id}:protocol_id")
        staker_weight = self.storage.get(f"staker:{staker}:{protocol_id}:amount", U128(0))
        if staker_weight == 0:
            raise ContractError.INSUFFICIENT_STAKE

        # Record vote
        self.storage.set(f"claim:{claim_id}:voted:{staker}", True)

        if vote_yes:
            yes_votes = self.storage.get(f"claim:{claim_id}:yes_votes", U128(0))
            self.storage.set(f"claim:{claim_id}:yes_votes", yes_votes + staker_weight)
        else:
            no_votes = self.storage.get(f"claim:{claim_id}:no_votes", U128(0))
            self.storage.set(f"claim:{claim_id}:no_votes", no_votes + staker_weight)

        self.env.emit_event("claim_vote_cast", {
            "claim_id": claim_id,
            "staker": staker,
            "weight": staker_weight,
            "vote_yes": vote_yes,
        })

    @external
    def finalize_claim(self, caller: Address, claim_id: U64):
        """Resolve a claim once its voting window closes. Anyone can trigger finalization."""
        caller.require_auth()
        self._require_initialized()

        policy_id = self.storage.get(f"claim:{claim_id}:policy_id", None)
        if policy_id is None:
            raise ContractError.CLAIM_NOT_FOUND

        if self.storage.get(f"claim:{claim_id}:resolved", False):
            raise ContractError.CLAIM_ALREADY_RESOLVED

        current_ledger = self.env.ledger().sequence()
        voting_end = self.storage.get(f"claim:{claim_id}:voting_end")
        if current_ledger <= voting_end:
            raise ContractError.VOTING_PERIOD_ACTIVE

        yes_votes = self.storage.get(f"claim:{claim_id}:yes_votes", U128(0))
        no_votes = self.storage.get(f"claim:{claim_id}:no_votes", U128(0))

        protocol_id = self.storage.get(f"policy:{policy_id}:protocol_id")
        total_staked = self.storage.get(f"protocol:{protocol_id}:total_staked", U128(0))

        approved = False
        payout_amount = U128(0)

        # Quorum & Decision: Requires > 50% of voting weight to approve claim.
        # At least 20% of total staked in protocol must vote (quorum target).
        total_votes = yes_votes + no_votes
        if total_votes * U128(5) >= total_staked:  # 20% quorum
            if yes_votes > no_votes:
                approved = True

        self.storage.set(f"claim:{claim_id}:resolved", True)

        if approved:
            payout_amount = self.storage.get(f"claim:{claim_id}:amount")
            # Enforce payout bounds
            if payout_amount > total_staked:
                payout_amount = total_staked

            holder = self.storage.get(f"policy:{policy_id}:holder")
            already_claimed = self.storage.get(f"policy:{policy_id}:claimed", U128(0))

            # Deduct payout from protocol staked pool
            self.storage.set(f"protocol:{protocol_id}:total_staked", total_staked - payout_amount)
            self.storage.set(f"policy:{policy_id}:claimed", already_claimed + payout_amount)

            # Transfer payout to holder
            asset_token = self.storage.get("asset_token")
            self.env.transfer(asset_token, self.env.current_contract(), holder, payout_amount)

            # Reduce active coverage
            active_coverage = self.storage.get(f"protocol:{protocol_id}:active_coverage", U128(0))
            if active_coverage >= payout_amount:
                self.storage.set(f"protocol:{protocol_id}:active_coverage", active_coverage - payout_amount)
            else:
                self.storage.set(f"protocol:{protocol_id}:active_coverage", U128(0))

        self.env.emit_event("claim_finalized", {
            "claim_id": claim_id,
            "approved": approved,
            "payout_amount": payout_amount,
            "yes_votes": yes_votes,
            "no_votes": no_votes,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_protocol(self, protocol_id: Symbol) -> Map:
        """Get protocol parameters and capital pool size."""
        if not self.storage.get(f"protocol:{protocol_id}:listed", False):
            raise ContractError.PROTOCOL_NOT_FOUND

        return {
            "protocol_id": protocol_id,
            "risk_weight": self.storage.get(f"protocol:{protocol_id}:risk_weight"),
            "max_capacity": self.storage.get(f"protocol:{protocol_id}:max_capacity"),
            "total_staked": self.storage.get(f"protocol:{protocol_id}:total_staked"),
            "active_coverage": self.storage.get(f"protocol:{protocol_id}:active_coverage"),
        }

    @view
    def get_policy(self, policy_id: U64) -> Map:
        """Get policy coverage and validity details."""
        holder = self.storage.get(f"policy:{policy_id}:holder", None)
        if holder is None:
            raise ContractError.POLICY_NOT_FOUND

        return {
            "policy_id": policy_id,
            "holder": holder,
            "protocol_id": self.storage.get(f"policy:{policy_id}:protocol_id"),
            "coverage": self.storage.get(f"policy:{policy_id}:coverage"),
            "expiry": self.storage.get(f"policy:{policy_id}:expiry"),
            "claimed": self.storage.get(f"policy:{policy_id}:claimed"),
        }

    @view
    def get_claim(self, claim_id: U64) -> Map:
        """Get claim details and voting tallies."""
        policy_id = self.storage.get(f"claim:{claim_id}:policy_id", None)
        if policy_id is None:
            raise ContractError.CLAIM_NOT_FOUND

        return {
            "claim_id": claim_id,
            "policy_id": policy_id,
            "amount": self.storage.get(f"claim:{claim_id}:amount"),
            "evidence": self.storage.get(f"claim:{claim_id}:evidence"),
            "voting_end": self.storage.get(f"claim:{claim_id}:voting_end"),
            "resolved": self.storage.get(f"claim:{claim_id}:resolved"),
            "yes_votes": self.storage.get(f"claim:{claim_id}:yes_votes"),
            "no_votes": self.storage.get(f"claim:{claim_id}:no_votes"),
        }

    @view
    def get_stake_balance(self, staker: Address, protocol_id: Symbol) -> Map:
        """Get staker's capital amount and lock status."""
        amount = self.storage.get(f"staker:{staker}:{protocol_id}:amount", U128(0))
        unlock_ledger = self.storage.get(f"staker:{staker}:{protocol_id}:unlock_ledger", U64(0))
        return {
            "amount": amount,
            "unlock_ledger": unlock_ledger,
        }

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED
