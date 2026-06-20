"""
Catastrophe Bond — Cat Bond subscription with principal loss waterfall.

Mycelium Smart Contract for Stellar
Enables insurance issuers to raise capital by issuing bonds. Investors deposit principal,
receive periodic coupon payments, and face potential principal drawdown (loss waterfall)
if an oracle reports a major catastrophic event (e.g. earthquake or hurricane).
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
    SUBSCRIPTION_CLOSED = 5
    SUBSCRIPTION_OPEN = 6
    MATURITY_REACHED = 7
    MATURITY_NOT_REACHED = 8
    ALREADY_CLAIMED = 9
    ORACLE_NOT_WHITELISTED = 10
    INSUFFICIENT_FUNDS = 11
    DISASTER_TRIGGERED = 12


class BondState:
    SUBSCRIPTION = 1
    ACTIVE = 2
    MATURED = 3


@contract
class CatastropheBond:
    """
    Catastrophe Bond contract managing subscriptions, scaled loss triggers,
    scalable interest coupon payouts, and post-maturity principal redemptions.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        issuer: Address,
        oracle: Address,
        asset_token: Address,
        target_principal: U128,
        maturity_ledger: U64,
        subscription_deadline: U64,
    ):
        """Initialize the catastrophe bond parameters."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        current_ledger = self.env.ledger().sequence()
        if subscription_deadline <= current_ledger or maturity_ledger <= subscription_deadline:
            raise ContractError.INVALID_PARAMETERS

        if target_principal == 0:
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("admin", admin)
        self.storage.set("issuer", issuer)
        self.storage.set("oracle", oracle)
        self.storage.set("asset_token", asset_token)
        self.storage.set("target_principal", target_principal)
        self.storage.set("maturity_ledger", maturity_ledger)
        self.storage.set("subscription_deadline", subscription_deadline)

        self.storage.set("total_subscribed", U128(0))
        self.storage.set("drawdown_bps", U64(0))  # Percentage of principal lost to disaster
        self.storage.set("state", BondState.SUBSCRIPTION)
        self.storage.set("accrued_coupon_per_share", U128(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "issuer": issuer,
            "target": target_principal,
            "maturity": maturity_ledger,
        })

    @external
    def subscribe(self, investor: Address, amount: U128):
        """Investors subscribe capital to the bond during the subscription phase."""
        investor.require_auth()
        self._require_initialized()

        state = self.storage.get("state")
        if state != BondState.SUBSCRIPTION:
            raise ContractError.SUBSCRIPTION_CLOSED

        current_ledger = self.env.ledger().sequence()
        deadline = self.storage.get("subscription_deadline")
        if current_ledger > deadline:
            raise ContractError.SUBSCRIPTION_CLOSED

        if amount == 0:
            raise ContractError.INVALID_PARAMETERS

        total_sub = self.storage.get("total_subscribed", U128(0))
        target = self.storage.get("target_principal")
        if total_sub + amount > target:
            raise ContractError.INVALID_PARAMETERS  # Cannot exceed cap

        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, investor, self.env.current_contract(), amount)

        # Record subscriber share
        sub_balance = self.storage.get(f"investor:{investor}:principal", U128(0))
        self.storage.set(f"investor:{investor}:principal", sub_balance + amount)
        self.storage.set(f"investor:{investor}:last_coupon_share", self.storage.get("accrued_coupon_per_share"))

        self.storage.set("total_subscribed", total_sub + amount)

        self.env.emit_event("bond_subscribed", {
            "investor": investor,
            "amount": amount,
            "total_subscribed": total_sub + amount,
        })

    @external
    def activate_bond(self, admin: Address):
        """Transition the bond from subscription phase to active phase."""
        admin.require_auth()
        self._require_initialized()

        state = self.storage.get("state")
        if state != BondState.SUBSCRIPTION:
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("state", BondState.ACTIVE)
        self.env.emit_event("bond_activated", {
            "total_subscribed": self.storage.get("total_subscribed"),
        })

    @external
    def pay_coupon(self, issuer: Address, coupon_pool_amount: U128):
        """Issuer pays periodic coupon interest, distributed proportionally to investors."""
        issuer.require_auth()
        self._require_initialized()

        # Check that caller is the issuer
        bond_issuer = self.storage.get("issuer")
        if issuer != bond_issuer:
            raise ContractError.UNAUTHORIZED

        state = self.storage.get("state")
        if state != BondState.ACTIVE:
            raise ContractError.INVALID_PARAMETERS

        total_sub = self.storage.get("total_subscribed", U128(0))
        if total_sub == 0 or coupon_pool_amount == 0:
            raise ContractError.INVALID_PARAMETERS

        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, issuer, self.env.current_contract(), coupon_pool_amount)

        # Distribute: accrued_coupon_per_share += (amount * 1e12) / total_subscribed
        # Using 1e12 scaling to handle float divisions safely
        scaling = U128(1000000000000)
        accrued = self.storage.get("accrued_coupon_per_share", U128(0))
        added = (coupon_pool_amount * scaling) // total_sub
        self.storage.set("accrued_coupon_per_share", accrued + added)

        self.env.emit_event("coupon_paid", {
            "amount": coupon_pool_amount,
            "accrued_per_share": accrued + added,
        })

    @external
    def claim_coupon(self, investor: Address):
        """Investor claims their accumulated coupon interest."""
        investor.require_auth()
        self._require_initialized()

        principal = self.storage.get(f"investor:{investor}:principal", U128(0))
        if principal == 0:
            raise ContractError.UNAUTHORIZED

        accrued = self.storage.get("accrued_coupon_per_share")
        last_share = self.storage.get(f"investor:{investor}:last_coupon_share", U128(0))

        if accrued <= last_share:
            raise ContractError.ALREADY_CLAIMED

        scaling = U128(1000000000000)
        diff = accrued - last_share
        claimable = (principal * diff) // scaling

        self.storage.set(f"investor:{investor}:last_coupon_share", accrued)

        if claimable > 0:
            asset_token = self.storage.get("asset_token")
            self.env.transfer(asset_token, self.env.current_contract(), investor, claimable)

        self.env.emit_event("coupon_claimed", {
            "investor": investor,
            "amount": claimable,
        })

    @external
    def trigger_disaster(
        self,
        oracle: Address,
        event_severity_scale: U64,  # e.g., earthquake magnitude * 10 (75 = 7.5 mag)
    ):
        """Oracle triggers disaster principal drawdown waterfall depending on event severity."""
        oracle.require_auth()
        self._require_initialized()

        whitelisted_oracle = self.storage.get("oracle")
        if oracle != whitelisted_oracle:
            raise ContractError.ORACLE_NOT_WHITELISTED

        state = self.storage.get("state")
        if state != BondState.ACTIVE:
            raise ContractError.INVALID_PARAMETERS

        # Waterfall rules:
        # severity < 70: 0% drawdown
        # severity >= 70 (< 75): 20% drawdown (2000 bps)
        # severity >= 75 (< 80): 50% drawdown (5000 bps)
        # severity >= 80: 100% drawdown (10000 bps)
        new_drawdown = U64(0)
        if event_severity_scale >= 80:
            new_drawdown = U64(10000)
        elif event_severity_scale >= 75:
            new_drawdown = U64(5000)
        elif event_severity_scale >= 70:
            new_drawdown = U64(2000)

        current_drawdown = self.storage.get("drawdown_bps", U64(0))
        # Enforce maximum single event drawdown (non-cumulative, but worst event takes precedence)
        if new_drawdown <= current_drawdown:
            raise ContractError.INVALID_PARAMETERS  # No increased damage reported

        diff_bps = new_drawdown - current_drawdown
        self.storage.set("drawdown_bps", new_drawdown)

        # Calculate principal amount to draw down to the issuer for relief
        total_sub = self.storage.get("total_subscribed")
        drawdown_amount = (total_sub * U128(diff_bps)) // U128(10000)

        if drawdown_amount > 0:
            issuer = self.storage.get("issuer")
            asset_token = self.storage.get("asset_token")
            # Transfer principal slice directly to issuer to pay insurance claims
            self.env.transfer(asset_token, self.env.current_contract(), issuer, drawdown_amount)

        self.env.emit_event("disaster_triggered", {
            "severity": event_severity_scale,
            "drawdown_bps": new_drawdown,
            "amount_withdrawn": drawdown_amount,
        })

    @external
    def mature_bond(self, caller: Address):
        """Transition bond to matured state once ledger maturity exceeds limit."""
        caller.require_auth()
        self._require_initialized()

        state = self.storage.get("state")
        if state != BondState.ACTIVE:
            raise ContractError.INVALID_PARAMETERS

        current_ledger = self.env.ledger().sequence()
        maturity = self.storage.get("maturity_ledger")
        if current_ledger < maturity:
            raise ContractError.MATURITY_NOT_REACHED

        self.storage.set("state", BondState.MATURED)
        self.env.emit_event("bond_matured", {
            "drawdown_bps": self.storage.get("drawdown_bps"),
        })

    @external
    def claim_principal_refund(self, investor: Address):
        """Investor claims remaining principal refund after maturity and potential drawdowns."""
        investor.require_auth()
        self._require_initialized()

        state = self.storage.get("state")
        if state != BondState.MATURED:
            raise ContractError.MATURITY_NOT_REACHED

        principal = self.storage.get(f"investor:{investor}:principal", U128(0))
        if principal == 0:
            raise ContractError.INVALID_PARAMETERS

        # Double check coupon claims before returning principal
        accrued = self.storage.get("accrued_coupon_per_share")
        last_share = self.storage.get(f"investor:{investor}:last_coupon_share", U128(0))
        if accrued > last_share:
            # Force auto-claim of remaining coupon first
            scaling = U128(1000000000000)
            diff = accrued - last_share
            claimable = (principal * diff) // scaling
            self.storage.set(f"investor:{investor}:last_coupon_share", accrued)
            if claimable > 0:
                asset_token = self.storage.get("asset_token")
                self.env.transfer(asset_token, self.env.current_contract(), investor, claimable)

        # Calculate remaining principal refund: principal * (10000 - drawdown_bps) / 10000
        drawdown_bps = self.storage.get("drawdown_bps", U64(0))
        refund_multiplier = U64(10000) - drawdown_bps
        refund_amount = (principal * U128(refund_multiplier)) // U128(10000)

        # Clear principal to prevent re-entrancy / double claiming
        self.storage.set(f"investor:{investor}:principal", U128(0))

        if refund_amount > 0:
            asset_token = self.storage.get("asset_token")
            self.env.transfer(asset_token, self.env.current_contract(), investor, refund_amount)

        self.env.emit_event("principal_refunded", {
            "investor": investor,
            "refund_amount": refund_amount,
            "slashed_amount": principal - refund_amount,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_bond_info(self) -> Map:
        """Get summary of the bond parameters and state."""
        return {
            "issuer": self.storage.get("issuer"),
            "oracle": self.storage.get("oracle"),
            "target": self.storage.get("target_principal"),
            "total_subscribed": self.storage.get("total_subscribed"),
            "maturity": self.storage.get("maturity_ledger"),
            "drawdown_bps": self.storage.get("drawdown_bps"),
            "state": self.storage.get("state"),
        }

    @view
    def get_investor_info(self, investor: Address) -> Map:
        """Get investor subscription details."""
        return {
            "principal": self.storage.get(f"investor:{investor}:principal", U128(0)),
            "last_coupon_share": self.storage.get(f"investor:{investor}:last_coupon_share", U128(0)),
        }

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
