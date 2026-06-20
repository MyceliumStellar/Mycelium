"""
Reinsurance — Excess of Loss (XOL) treaties and retrocession pool.

Mycelium Smart Contract for Stellar
Provides reinsurance treaties for primary insurers (cedants). Manages Excess of Loss
retention limits, retrocession pool sharing, bordereaux premium reports, and loss reserve tracking.
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
    TREATY_NOT_FOUND = 5
    TREATY_ALREADY_EXISTS = 6
    AGGREGATE_LIMIT_EXCEEDED = 7
    INSUFFICIENT_POOL_RESERVES = 8
    RETROCESSION_LIMIT_EXCEEDED = 9
    MEMBER_NOT_FOUND = 10


@contract
class Reinsurance:
    """
    Reinsurance contract managing Excess of Loss treaties, bordereaux reports,
    loss reserves, and a retrocession capital pool.
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
        """Initialize the reinsurance contract."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("asset_token", asset_token)
        self.storage.set("total_retrocession_bps", U64(0))
        self.storage.set("outstanding_reserves", U128(0))  # Loss reserves
        self.storage.set("premium_reserves", U128(0))      # Premium reserves
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "asset_token": asset_token,
        })

    @external
    def create_xol_treaty(
        self,
        admin: Address,
        cedant: Address,
        treaty_id: Symbol,
        retention: U128,
        cover_limit: U128,
        aggregate_limit: U128,
        premium_rate_bps: U64,
    ):
        """Establish an Excess of Loss treaty with a primary insurer (cedant)."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        if retention == 0 or cover_limit == 0 or aggregate_limit == 0 or premium_rate_bps == 0:
            raise ContractError.INVALID_PARAMETERS

        if self.storage.get(f"treaty:{treaty_id}:exists", False):
            raise ContractError.TREATY_ALREADY_EXISTS

        self.storage.set(f"treaty:{treaty_id}:exists", True)
        self.storage.set(f"treaty:{treaty_id}:cedant", cedant)
        self.storage.set(f"treaty:{treaty_id}:retention", retention)
        self.storage.set(f"treaty:{treaty_id}:cover_limit", cover_limit)
        self.storage.set(f"treaty:{treaty_id}:aggregate_limit", aggregate_limit)
        self.storage.set(f"treaty:{treaty_id}:premium_rate", premium_rate_bps)
        self.storage.set(f"treaty:{treaty_id}:total_payouts", U128(0))

        self.env.emit_event("treaty_created", {
            "treaty_id": treaty_id,
            "cedant": cedant,
            "retention": retention,
            "cover_limit": cover_limit,
            "aggregate_limit": aggregate_limit,
        })

    @external
    def submit_bordereau_report(
        self,
        cedant: Address,
        treaty_id: Symbol,
        report_id: U64,
        premium_volume: U128,
        outstanding_losses: U128,
    ):
        """Cedant reports periodic bordereaux summary of written premiums and losses."""
        cedant.require_auth()
        self._require_initialized()

        if not self.storage.get(f"treaty:{treaty_id}:exists", False):
            raise ContractError.TREATY_NOT_FOUND

        treaty_cedant = self.storage.get(f"treaty:{treaty_id}:cedant")
        if treaty_cedant != cedant:
            raise ContractError.UNAUTHORIZED

        # Calculate reinsurance premium due
        premium_rate = self.storage.get(f"treaty:{treaty_id}:premium_rate")
        reinsurance_premium = (premium_volume * U128(premium_rate)) // U128(10000)

        asset_token = self.storage.get("asset_token")
        # Collect premium from the primary insurer
        if reinsurance_premium > 0:
            self.env.transfer(asset_token, cedant, self.env.current_contract(), reinsurance_premium)

        # Update premium reserves and outstanding loss reserve estimates
        prem_reserves = self.storage.get("premium_reserves", U128(0))
        self.storage.set("premium_reserves", prem_reserves + reinsurance_premium)

        out_reserves = self.storage.get("outstanding_reserves", U128(0))
        self.storage.set("outstanding_reserves", out_reserves + outstanding_losses)

        # Retrocession fee splitting: payout shares of the premium to retrocessionaires
        total_retro_bps = self.storage.get("total_retrocession_bps", U64(0))
        if total_retro_bps > 0 and reinsurance_premium > 0:
            retro_premium = (reinsurance_premium * U128(total_retro_bps)) // U128(10000)
            self.storage.set("premium_reserves", self.storage.get("premium_reserves") - retro_premium)

            # Keep track of retrocession pool total reserves
            retro_reserves = self.storage.get("retrocession_reserves", U128(0))
            self.storage.set("retrocession_reserves", retro_reserves + retro_premium)

        self.env.emit_event("bordereau_submitted", {
            "treaty_id": treaty_id,
            "report_id": report_id,
            "reinsurance_premium": reinsurance_premium,
            "reported_losses": outstanding_losses,
        })

    @external
    def join_retrocession_pool(
        self,
        retrocessionaire: Address,
        share_bps: U64,
        capital_amount: U128,
    ):
        """Retrocessionaires deposit capital to share premium returns and reinsurance losses."""
        retrocessionaire.require_auth()
        self._require_initialized()

        if share_bps == 0 or capital_amount == 0:
            raise ContractError.INVALID_PARAMETERS

        total_retro_bps = self.storage.get("total_retrocession_bps", U64(0))
        new_total_bps = total_retro_bps + share_bps
        if new_total_bps > 10000:
            raise ContractError.RETROCESSION_LIMIT_EXCEEDED

        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, retrocessionaire, self.env.current_contract(), capital_amount)

        self.storage.set("total_retrocession_bps", new_total_bps)

        current_share = self.storage.get(f"retro:{retrocessionaire}:share", U64(0))
        self.storage.set(f"retro:{retrocessionaire}:share", current_share + share_bps)

        current_capital = self.storage.get(f"retro:{retrocessionaire}:capital", U128(0))
        self.storage.set(f"retro:{retrocessionaire}:capital", current_capital + capital_amount)

        self.env.emit_event("retrocessionaire_joined", {
            "retrocessionaire": retrocessionaire,
            "share_bps": share_bps,
            "capital_amount": capital_amount,
        })

    @external
    def settle_treaty_loss(
        self,
        cedant: Address,
        treaty_id: Symbol,
        single_loss_amount: U128,
    ):
        """Settle a claim where loss exceeds retention limit under XOL treaty."""
        cedant.require_auth()
        self._require_initialized()

        if not self.storage.get(f"treaty:{treaty_id}:exists", False):
            raise ContractError.TREATY_NOT_FOUND

        treaty_cedant = self.storage.get(f"treaty:{treaty_id}:cedant")
        if treaty_cedant != cedant:
            raise ContractError.UNAUTHORIZED

        retention = self.storage.get(f"treaty:{treaty_id}:retention")
        if single_loss_amount <= retention:
            raise ContractError.INVALID_PARAMETERS  # loss below retention

        # Calculate Excess Loss
        excess_loss = single_loss_amount - retention
        cover_limit = self.storage.get(f"treaty:{treaty_id}:cover_limit")

        payout_amount = excess_loss
        if payout_amount > cover_limit:
            payout_amount = cover_limit

        # Validate Aggregate Limit bounds
        total_payouts = self.storage.get(f"treaty:{treaty_id}:total_payouts", U128(0))
        aggregate_limit = self.storage.get(f"treaty:{treaty_id}:aggregate_limit")

        if total_payouts + payout_amount > aggregate_limit:
            # Scale down to remaining aggregate limit capacity
            payout_amount = aggregate_limit - total_payouts

        if payout_amount == 0:
            raise ContractError.AGGREGATE_LIMIT_EXCEEDED

        # Check total contract solvency (premium reserves + retrocession reserves)
        premium_reserves = self.storage.get("premium_reserves", U128(0))
        retrocession_reserves = self.storage.get("retrocession_reserves", U128(0))

        total_funds = premium_reserves + retrocession_reserves
        if payout_amount > total_funds:
            raise ContractError.INSUFFICIENT_POOL_RESERVES

        # Deduct payout split: Reinsurance main vs Retrocession pool
        total_retro_bps = self.storage.get("total_retrocession_bps", U64(0))
        retro_share_amount = (payout_amount * U128(total_retro_bps)) // U128(10000)
        reinsurance_share_amount = payout_amount - retro_share_amount

        # Apply deductions
        self.storage.set("premium_reserves", premium_reserves - reinsurance_share_amount)
        self.storage.set("retrocession_reserves", retrocession_reserves - retro_share_amount)
        self.storage.set(f"treaty:{treaty_id}:total_payouts", total_payouts + payout_amount)

        # Deduct from outstanding reserves if reported before
        outstanding = self.storage.get("outstanding_reserves", U128(0))
        if outstanding >= payout_amount:
            self.storage.set("outstanding_reserves", outstanding - payout_amount)
        else:
            self.storage.set("outstanding_reserves", U128(0))

        # Transfer payout to the cedant
        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, self.env.current_contract(), cedant, payout_amount)

        self.env.emit_event("treaty_loss_settled", {
            "treaty_id": treaty_id,
            "cedant": cedant,
            "total_loss": single_loss_amount,
            "reinsurance_payout": payout_amount,
            "retrocession_share": retro_share_amount,
        })

    @external
    def withdraw_retrocession(
        self,
        retrocessionaire: Address,
        amount: U128,
    ):
        """Retrocessionaire withdraws capital or premium share profits."""
        retrocessionaire.require_auth()
        self._require_initialized()

        capital = self.storage.get(f"retro:{retrocessionaire}:capital", U128(0))
        if capital == 0:
            raise ContractError.MEMBER_NOT_FOUND

        # Retrocession reserves check (cannot withdraw below required ratio or outstanding claims)
        # Simplify: can withdraw up to their current share of premium reserves + deposited capital
        share_bps = self.storage.get(f"retro:{retrocessionaire}:share", U64(0))
        retro_reserves = self.storage.get("retrocession_reserves", U128(0))
        share_reserves = (retro_reserves * U128(share_bps)) // U128(10000)

        total_withdrawable = capital + share_reserves
        if amount > total_withdrawable:
            raise ContractError.INVALID_PARAMETERS

        # Update states
        if amount <= capital:
            self.storage.set(f"retro:{retrocessionaire}:capital", capital - amount)
        else:
            excess = amount - capital
            self.storage.set(f"retro:{retrocessionaire}:capital", U128(0))
            self.storage.set("retrocession_reserves", retro_reserves - excess)

        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, self.env.current_contract(), retrocessionaire, amount)

        self.env.emit_event("retrocession_withdrawn", {
            "retrocessionaire": retrocessionaire,
            "amount": amount,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_treaty(self, treaty_id: Symbol) -> Map:
        """Retrieve reinsurance treaty details."""
        if not self.storage.get(f"treaty:{treaty_id}:exists", False):
            raise ContractError.TREATY_NOT_FOUND

        return {
            "treaty_id": treaty_id,
            "cedant": self.storage.get(f"treaty:{treaty_id}:cedant"),
            "retention": self.storage.get(f"treaty:{treaty_id}:retention"),
            "cover_limit": self.storage.get(f"treaty:{treaty_id}:cover_limit"),
            "aggregate_limit": self.storage.get(f"treaty:{treaty_id}:aggregate_limit"),
            "premium_rate": self.storage.get(f"treaty:{treaty_id}:premium_rate"),
            "total_payouts": self.storage.get(f"treaty:{treaty_id}:total_payouts"),
        }

    @view
    def get_reserves(self) -> Map:
        """Get outstanding claim reserves and premium reserves."""
        return {
            "outstanding_reserves": self.storage.get("outstanding_reserves", U128(0)),
            "premium_reserves": self.storage.get("premium_reserves", U128(0)),
            "retrocession_reserves": self.storage.get("retrocession_reserves", U128(0)),
        }

    @view
    def get_retro_info(self, retrocessionaire: Address) -> Map:
        """Get retrocessionaire's share parameters and capital."""
        return {
            "share_bps": self.storage.get(f"retro:{retrocessionaire}:share", U64(0)),
            "capital": self.storage.get(f"retro:{retrocessionaire}:capital", U128(0)),
        }

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED
