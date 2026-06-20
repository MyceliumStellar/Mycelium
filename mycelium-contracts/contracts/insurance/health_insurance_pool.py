"""
Health Insurance Pool — Tiered health insurance coverage with deductible tracking.

Mycelium Smart Contract for Stellar
Handles patient enrollments, dependent rules, premium collections, out-of-pocket
deductible tracking, co-pay splits, and medical provider claim settlements.
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
    PROVIDER_NOT_WHITELISTED = 5
    ENROLLMENT_LIMIT_REACHED = 6
    POLICY_NOT_FOUND = 7
    POLICY_EXPIRED = 8
    POLICY_ALREADY_EXISTS = 9
    INSUFFICIENT_FUNDS = 10
    INSUFFICIENT_POOL_BALANCE = 11
    MAXIMUM_BOUND_EXCEEDED = 12


class HealthTier:
    SILVER = 1
    GOLD = 2
    PLATINUM = 3


@contract
class HealthInsurancePool:
    """
    Tiered health insurance pool for managing members, dependent extensions,
    deductible limits, co-pays, and payouts to healthcare providers.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        asset_token: Address,
        max_enrollees: U64,
        policy_duration_ledgers: U64,
    ):
        """Initialize the health insurance pool contract."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if max_enrollees == 0 or policy_duration_ledgers == 0:
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("admin", admin)
        self.storage.set("asset_token", asset_token)
        self.storage.set("max_enrollees", max_enrollees)
        self.storage.set("policy_duration", policy_duration_ledgers)
        self.storage.set("policy_count", U64(0))
        self.storage.set("enrollees_count", U64(0))
        self.storage.set("pool_balance", U128(0))
        self.storage.set("initialized", True)

        # Set Tier Parameters: (Premium, Deductible, Copay bps, Max Benefit Limit)
        # Silver Tier: High Deductible, High Copay (30%)
        self._set_tier_params(HealthTier.SILVER, U128(100), U128(5000), U64(3000), U128(50000))
        # Gold Tier: Medium Deductible, Medium Copay (20%)
        self._set_tier_params(HealthTier.GOLD, U128(250), U128(2000), U64(2000), U128(150000))
        # Platinum Tier: Low Deductible, Low Copay (10%)
        self._set_tier_params(HealthTier.PLATINUM, U128(500), U128(500), U64(1000), U128(500000))

        self.env.emit_event("initialized", {
            "admin": admin,
            "asset_token": asset_token,
            "max_enrollees": max_enrollees,
        })

    @external
    def whitelist_provider(self, admin: Address, provider: Address):
        """Whitelist a trusted healthcare/medical provider."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        self.storage.set(f"provider:{provider}:whitelisted", True)
        self.env.emit_event("provider_whitelisted", {"provider": provider})

    @external
    def remove_provider(self, admin: Address, provider: Address):
        """Remove a medical provider from the whitelist."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        self.storage.set(f"provider:{provider}:whitelisted", False)
        self.env.emit_event("provider_removed", {"provider": provider})

    @external
    def enroll_member(
        self,
        member: Address,
        tier: U64,
        dependents_count: U64,
    ) -> U64:
        """Enroll a primary member and their dependents into a health tier."""
        member.require_auth()
        self._require_initialized()

        enrollees_count = self.storage.get("enrollees_count", U64(0))
        max_enrollees = self.storage.get("max_enrollees")
        if enrollees_count >= max_enrollees:
            raise ContractError.ENROLLMENT_LIMIT_REACHED

        if tier not in (HealthTier.SILVER, HealthTier.GOLD, HealthTier.PLATINUM):
            raise ContractError.INVALID_PARAMETERS

        # Get tier params
        base_premium = self.storage.get(f"tier:{tier}:premium")
        # Dependent rules: +50% premium per dependent
        multiplier = U128(100) + U128(dependents_count * 50)
        total_premium = (base_premium * multiplier) // U128(100)

        # Collect premium
        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, member, self.env.current_contract(), total_premium)

        # Update pool balance
        pool_balance = self.storage.get("pool_balance", U128(0))
        self.storage.set("pool_balance", pool_balance + total_premium)

        # Record policy
        policy_id = self.storage.get("policy_count") + 1
        self.storage.set("policy_count", policy_id)
        self.storage.set("enrollees_count", enrollees_count + 1)

        expiry_ledger = self.env.ledger().sequence() + self.storage.get("policy_duration")

        self.storage.set(f"policy:{policy_id}:member", member)
        self.storage.set(f"policy:{policy_id}:tier", tier)
        self.storage.set(f"policy:{policy_id}:dependents", dependents_count)
        self.storage.set(f"policy:{policy_id}:expiry", expiry_ledger)
        self.storage.set(f"policy:{policy_id}:deductible_accumulated", U128(0))
        self.storage.set(f"policy:{policy_id}:benefits_paid", U128(0))

        self.env.emit_event("member_enrolled", {
            "policy_id": policy_id,
            "member": member,
            "tier": tier,
            "premium": total_premium,
            "expiry": expiry_ledger,
        })

        return policy_id

    @external
    def submit_provider_claim(
        self,
        provider: Address,
        policy_id: U64,
        bill_amount: U128,
        medical_code: Bytes,
    ):
        """Submit a medical claim by a whitelisted provider on behalf of a policy ID."""
        provider.require_auth()
        self._require_initialized()

        if not self.storage.get(f"provider:{provider}:whitelisted", False):
            raise ContractError.PROVIDER_NOT_WHITELISTED

        member = self.storage.get(f"policy:{policy_id}:member", None)
        if member is None:
            raise ContractError.POLICY_NOT_FOUND

        expiry = self.storage.get(f"policy:{policy_id}:expiry")
        if self.env.ledger().sequence() > expiry:
            raise ContractError.POLICY_EXPIRED

        tier = self.storage.get(f"policy:{policy_id}:tier")
        deductible_limit = self.storage.get(f"tier:{tier}:deductible")
        deductible_paid = self.storage.get(f"policy:{policy_id}:deductible_accumulated", U128(0))
        copay_bps = self.storage.get(f"tier:{tier}:copay")
        max_limit = self.storage.get(f"tier:{tier}:max_limit")
        benefits_paid = self.storage.get(f"policy:{policy_id}:benefits_paid", U128(0))

        # Check maximum benefit constraint
        if benefits_paid >= max_limit:
            raise ContractError.MAXIMUM_BOUND_EXCEEDED

        # Track out of pocket / Deductible calculations
        remaining_deductible = U128(0)
        if deductible_paid < deductible_limit:
            remaining_deductible = deductible_limit - deductible_paid

        patient_share = U128(0)
        pool_share = U128(0)

        if bill_amount <= remaining_deductible:
            # Bill is completely under the deductible, patient pays 100% out of pocket
            # (which counts toward their deductible balance)
            patient_share = bill_amount
            deductible_paid += bill_amount
        else:
            # Portion of the bill is under the deductible, remainder is subject to co-pay
            deductible_portion = remaining_deductible
            copay_portion = bill_amount - deductible_portion

            deductible_paid = deductible_limit

            # Split copay portion based on tier copay rate
            patient_copay = (copay_portion * U128(copay_bps)) // U128(10000)
            pool_copay = copay_portion - patient_copay

            patient_share = deductible_portion + patient_copay
            pool_share = pool_copay

        # Ensure pool share doesn't exceed maximum benefit bounds
        if benefits_paid + pool_share > max_limit:
            pool_share = max_limit - benefits_paid
            # Rest goes to patient out-of-pocket
            patient_share = bill_amount - pool_share

        # Update policy states
        self.storage.set(f"policy:{policy_id}:deductible_accumulated", deductible_paid)
        self.storage.set(f"policy:{policy_id}:benefits_paid", benefits_paid + pool_share)

        pool_balance = self.storage.get("pool_balance", U128(0))
        if pool_share > 0:
            if pool_share > pool_balance:
                pool_share = pool_balance  # bounded by solvency
            
            self.storage.set("pool_balance", pool_balance - pool_share)
            asset_token = self.storage.get("asset_token")
            # Pay the healthcare provider the pool coverage portion
            self.env.transfer(asset_token, self.env.current_contract(), provider, pool_share)

        self.env.emit_event("medical_claim_processed", {
            "policy_id": policy_id,
            "provider": provider,
            "bill_amount": bill_amount,
            "pool_share": pool_share,
            "patient_share": patient_share,
            "medical_code": medical_code,
        })

    @external
    def withdraw_surplus(self, admin: Address, recipient: Address, amount: U128):
        """Withdraw excess funds from the pool balance by admin."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        pool_balance = self.storage.get("pool_balance", U128(0))
        if amount > pool_balance:
            raise ContractError.INSUFFICIENT_POOL_BALANCE

        self.storage.set("pool_balance", pool_balance - amount)
        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, self.env.current_contract(), recipient, amount)

        self.env.emit_event("surplus_withdrawn", {
            "recipient": recipient,
            "amount": amount,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_policy(self, policy_id: U64) -> Map:
        """Retrieve full member policy details and accumulation state."""
        member = self.storage.get(f"policy:{policy_id}:member", None)
        if member is None:
            raise ContractError.POLICY_NOT_FOUND

        return {
            "policy_id": policy_id,
            "member": member,
            "tier": self.storage.get(f"policy:{policy_id}:tier"),
            "dependents": self.storage.get(f"policy:{policy_id}:dependents"),
            "expiry": self.storage.get(f"policy:{policy_id}:expiry"),
            "deductible_accumulated": self.storage.get(f"policy:{policy_id}:deductible_accumulated"),
            "benefits_paid": self.storage.get(f"policy:{policy_id}:benefits_paid"),
        }

    @view
    def get_tier_info(self, tier: U64) -> Map:
        """Get parameters of a health tier."""
        if tier not in (HealthTier.SILVER, HealthTier.GOLD, HealthTier.PLATINUM):
            raise ContractError.INVALID_PARAMETERS

        return {
            "premium": self.storage.get(f"tier:{tier}:premium"),
            "deductible": self.storage.get(f"tier:{tier}:deductible"),
            "copay": self.storage.get(f"tier:{tier}:copay"),
            "max_limit": self.storage.get(f"tier:{tier}:max_limit"),
        }

    @view
    def is_provider_whitelisted(self, provider: Address) -> Bool:
        """Check if provider is whitelisted."""
        return self.storage.get(f"provider:{provider}:whitelisted", False)

    @view
    def get_pool_balance(self) -> U128:
        """Get current pool reserve balance."""
        return self.storage.get("pool_balance", U128(0))

    # ── Private Helpers ───────────────────────────────────────────────

    def _set_tier_params(self, tier: U64, premium: U128, deductible: U128, copay: U64, max_limit: U128):
        self.storage.set(f"tier:{tier}:premium", premium)
        self.storage.set(f"tier:{tier}:deductible", deductible)
        self.storage.set(f"tier:{tier}:copay", copay)
        self.storage.set(f"tier:{tier}:max_limit", max_limit)

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED
