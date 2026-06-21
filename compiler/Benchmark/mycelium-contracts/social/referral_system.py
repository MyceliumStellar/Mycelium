"""
Referral System — Multi-tier referral linking with loop check and fee rebate distribution.

Mycelium Smart Contract for Stellar
Handles multi-tier (direct and indirect) referral links, performs cycle/loop
validation on link creation, and distributes fee rebates and referral cuts.
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
    REFERRAL_LOOP = 5
    ALREADY_REFERRED = 6
    TRANSFER_FAILED = 7


@contract
class ReferralSystem:
    """
    Referral system that prevents direct or nested reference loops,
    calculates direct and indirect commission splits, and issues buyer rebates.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        base_asset: Address,
        direct_bps: U64,    # e.g., 500 bps = 5% direct commission
        indirect_bps: U64,  # e.g., 200 bps = 2% indirect commission
        rebate_bps: U64,    # e.g., 100 bps = 1% buyer rebate
    ):
        """Initialize the referral system parameters."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if direct_bps + indirect_bps + rebate_bps > 5000:  # Combined reward cannot exceed 50%
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("admin", admin)
        self.storage.set("base_asset", base_asset)
        self.storage.set("direct_bps", direct_bps)
        self.storage.set("indirect_bps", indirect_bps)
        self.storage.set("rebate_bps", rebate_bps)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "base_asset": base_asset,
        })

    @external
    def link_referrer(self, user: Address, referrer: Address):
        """Link a user to their referrer. Enforces loop detection up to 10 levels deep."""
        user.require_auth()
        self._require_initialized()

        if user == referrer:
            raise ContractError.REFERRAL_LOOP

        if self.storage.get(f"referred_by:{user}", None) is not None:
            raise ContractError.ALREADY_REFERRED

        # Loop check traversal: trace back from referrer to make sure they do not lead to user
        current = referrer
        for i in range(10):  # Maximum depth of 10 to protect gas limit
            parent = self.storage.get(f"referred_by:{current}", None)
            if parent is None:
                break
            if parent == user:
                raise ContractError.REFERRAL_LOOP
            current = parent

        self.storage.set(f"referred_by:{user}", referrer)

        # Update statistics
        ref_count = self.storage.get(f"referred_count:{referrer}", U64(0))
        self.storage.set(f"referred_count:{referrer}", ref_count + 1)

        self.env.emit_event("referrer_linked", {
            "user": user,
            "referrer": referrer,
        })

    @external
    def distribute_fee(
        self,
        caller: Address,
        buyer: Address,
        fee_amount: U128,
    ):
        """Split a transaction fee between direct/indirect referrers, buyer rebate, and admin."""
        caller.require_auth()
        self._require_initialized()

        if fee_amount == 0:
            raise ContractError.INVALID_PARAMETERS

        base_asset = self.storage.get("base_asset")
        admin = self.storage.get("admin")

        direct_bps = self.storage.get("direct_bps")
        indirect_bps = self.storage.get("indirect_bps")
        rebate_bps = self.storage.get("rebate_bps")

        direct_ref = self.storage.get(f"referred_by:{buyer}", None)

        direct_share = U128(0)
        indirect_share = U128(0)
        rebate_share = U128(0)

        # 1. Direct commission & buyer rebate
        if direct_ref is not None:
            direct_share = (fee_amount * U128(direct_bps)) // U128(10000)
            rebate_share = (fee_amount * U128(rebate_bps)) // U128(10000)

            # 2. Indirect commission (referrer's referrer)
            indirect_ref = self.storage.get(f"referred_by:{direct_ref}", None)
            if indirect_ref is not None:
                indirect_share = (fee_amount * U128(indirect_bps)) // U128(10000)

        admin_share = fee_amount - (direct_share + indirect_share + rebate_share)

        # Perform transfers (caller must have approved the contract to pull the fee)
        # Pull total fee to contract
        self.env.transfer(base_asset, caller, self.env.current_contract(), fee_amount)

        # Disburse parts
        if direct_share > 0:
            self.env.transfer(base_asset, self.env.current_contract(), direct_ref, direct_share)
            d_total = self.storage.get(f"user_earnings:{direct_ref}:direct", U128(0))
            self.storage.set(f"user_earnings:{direct_ref}:direct", d_total + direct_share)

        if indirect_share > 0:
            self.env.transfer(base_asset, self.env.current_contract(), indirect_ref, indirect_share)
            ind_total = self.storage.get(f"user_earnings:{indirect_ref}:indirect", U128(0))
            self.storage.set(f"user_earnings:{indirect_ref}:indirect", ind_total + indirect_share)

        if rebate_share > 0:
            self.env.transfer(base_asset, self.env.current_contract(), buyer, rebate_share)
            reb_total = self.storage.get(f"user_earnings:{buyer}:rebate", U128(0))
            self.storage.set(f"user_earnings:{buyer}:rebate", reb_total + rebate_share)

        if admin_share > 0:
            self.env.transfer(base_asset, self.env.current_contract(), admin, admin_share)

        self.env.emit_event("referral_fee_distributed", {
            "buyer": buyer,
            "total_fee": fee_amount,
            "direct_ref": direct_ref,
            "direct_share": direct_share,
            "indirect_share": indirect_share,
            "rebate_share": rebate_share,
            "admin_share": admin_share,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_referrer(self, user: Address) -> Address:
        """Get direct referrer of a user."""
        return self.storage.get(f"referred_by:{user}", None)

    @view
    def get_referred_count(self, user: Address) -> U64:
        """Get number of direct accounts referred by user."""
        return self.storage.get(f"referred_count:{user}", U64(0))

    @view
    def get_referral_earnings(self, user: Address) -> Map:
        """Retrieve complete breakdown of referral commissions and rebates."""
        return {
            "direct_earnings": self.storage.get(f"user_earnings:{user}:direct", U128(0)),
            "indirect_earnings": self.storage.get(f"user_earnings:{user}:indirect", U128(0)),
            "rebates": self.storage.get(f"user_earnings:{user}:rebate", U128(0)),
        }

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
