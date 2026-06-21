"""
Structured Product — Principal guarantees, yield structures, and option payoffs.

Mycelium Smart Contract for Stellar. Allows users to invest stablecoins into
structured products. Enforces subscription periods, guarantees principal returns (e.g. 95%),
accrues interest yields, and pays out variable option payoffs at maturity based on oracle spot prices.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)

class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    PAUSED = 4
    PRODUCT_NOT_FOUND = 5
    PRODUCT_EXPIRED = 6
    SUBSCRIPTION_CLOSED = 7
    NOT_MATURED = 8
    ALREADY_SETTLED = 9
    ALREADY_CLAIMED = 10
    ORACLE_READ_FAILED = 11
    INVALID_AMOUNT = 12

# Product Status
STATUS_SUBSCRIBING = U64(0)
STATUS_ACTIVE = U64(1)
STATUS_SETTLED = U64(2)

@contract
class StructuredProduct:
    """
    Structured yield investment product with principal protection and options upside.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        oracle: Address,
        stablecoin: Address,
        yield_rate_bps: U64 # Annualized interest rate earned on locked principal (e.g. 600 for 6%)
    ):
        """Initialize product administration, oracle, and baseline yield parameters."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("oracle", oracle)
        self.storage.set("stablecoin", stablecoin)
        self.storage.set("yield_rate_bps", yield_rate_bps)
        self.storage.set("product_next_id", U64(1))
        
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "oracle": oracle,
            "stablecoin": stablecoin,
            "yield_rate_bps": yield_rate_bps
        })

    @external
    def create_product(
        self,
        caller: Address,
        market: Symbol,
        strike_price: U128,
        subscription_deadline: U64,
        maturity: U64,
        participation_bps: U64,        # e.g. 8000 for 80% participation in option upside
        principal_guarantee_pct: U64  # e.g. 95 for 95% guaranteed return
    ) -> U64:
        """
        Create a new structured investment product (Admin only).
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()
        self._require_admin(caller)

        now = self._get_now()
        if subscription_deadline <= now or maturity <= subscription_deadline:
            raise ContractError.PRODUCT_EXPIRED

        product_id = self.storage.get("product_next_id", U64(1))
        self.storage.set("product_next_id", product_id + U64(1))

        # Store product details
        self.storage.set(f"prod_market_{product_id}", market)
        self.storage.set(f"prod_strike_{product_id}", strike_price)
        self.storage.set(f"prod_deadline_{product_id}", subscription_deadline)
        self.storage.set(f"prod_maturity_{product_id}", maturity)
        self.storage.set(f"prod_participation_{product_id}", participation_bps)
        self.storage.set(f"prod_guarantee_{product_id}", principal_guarantee_pct)
        self.storage.set(f"prod_status_{product_id}", STATUS_SUBSCRIBING)
        self.storage.set(f"prod_total_invested_{product_id}", U128(0))

        self.env.emit_event("product_created", {
            "product_id": product_id,
            "market": market,
            "strike_price": strike_price,
            "maturity": maturity,
            "guarantee_pct": principal_guarantee_pct
        })

        return product_id

    @external
    def invest(self, caller: Address, product_id: U64, amount: U128):
        """
        Invest stablecoins into a structured product during its subscription window.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        status = self.storage.get(f"prod_status_{product_id}")
        if status is None:
            raise ContractError.PRODUCT_NOT_FOUND

        if status != STATUS_SUBSCRIBING:
            raise ContractError.SUBSCRIPTION_CLOSED

        deadline = self.storage.get(f"prod_deadline_{product_id}", U64(0))
        if self._get_now() >= deadline:
            # Auto-transition status if deadline passed
            self.storage.set(f"prod_status_{product_id}", STATUS_ACTIVE)
            raise ContractError.SUBSCRIPTION_CLOSED

        if amount == U128(0):
            raise ContractError.INVALID_AMOUNT

        token = self.storage.get("stablecoin")
        contract_addr = self.env.current_contract_address()

        # Transfer funds from investor to contract
        self.env.call(token, "transfer", caller, contract_addr, amount)

        # Update investment records
        invested = self.storage.get(f"prod_total_invested_{product_id}", U128(0))
        self.storage.set(f"prod_total_invested_{product_id}", invested + amount)

        user_investment = self.storage.get(f"prod_invested_{product_id}_{caller}", U128(0))
        self.storage.set(f"prod_invested_{product_id}_{caller}", user_investment + amount)

        self.env.emit_event("invested", {
            "product_id": product_id,
            "investor": caller,
            "amount": amount
        })

    @external
    def settle_product(self, product_id: U64):
        """
        Settle the structured product at maturity (Public access).
        Calculates fixed interest yields and option payoffs based on oracle spot price.
        """
        self._require_initialized()

        status = self.storage.get(f"prod_status_{product_id}")
        if status is None:
            raise ContractError.PRODUCT_NOT_FOUND
        if status == STATUS_SETTLED:
            raise ContractError.ALREADY_SETTLED

        maturity = self.storage.get(f"prod_maturity_{product_id}", U64(0))
        if self._get_now() < maturity:
            raise ContractError.PRODUCT_NOT_FOUND

        # Fetch oracle spot price
        market = self.storage.get(f"prod_market_{product_id}")
        spot_price = self._get_oracle_price(market)
        strike_price = self.storage.get(f"prod_strike_{product_id}", U128(0))

        # Calculate option performance: payoff_factor = max(0, spot - strike) / strike
        payoff_factor_bps = U128(0)
        if spot_price > strike_price:
            payoff_factor_bps = ((spot_price - strike_price) * U128(10000)) / strike_price

        # Calculate fixed interest yield earned over lock duration
        deadline = self.storage.get(f"prod_deadline_{product_id}", U64(0))
        duration = maturity - deadline
        yield_rate = self.storage.get("yield_rate_bps", U64(0))
        # Yield = yield_rate * duration / (365 * 24 * 3600 * 10000)
        yearly_seconds = U128(31_536_000 * 10000)
        interest_bps = (U128(yield_rate) * U128(int(duration)) * U128(10000)) / yearly_seconds

        self.storage.set(f"prod_settled_payoff_bps_{product_id}", payoff_factor_bps)
        self.storage.set(f"prod_settled_interest_bps_{product_id}", interest_bps)
        self.storage.set(f"prod_status_{product_id}", STATUS_SETTLED)

        self.env.emit_event("product_settled", {
            "product_id": product_id,
            "spot_price": spot_price,
            "payoff_bps": payoff_factor_bps,
            "interest_bps": interest_bps
        })

    @external
    def claim_funds(self, caller: Address, product_id: U64):
        """
        Withdraw principal guarantee plus yield and option payouts after maturity settlement.
        """
        caller.require_auth()
        self._require_initialized()

        status = self.storage.get(f"prod_status_{product_id}")
        if status is None:
            raise ContractError.PRODUCT_NOT_FOUND
        if status != STATUS_SETTLED:
            raise ContractError.NOT_MATURED

        investment = self.storage.get(f"prod_invested_{product_id}_{caller}", U128(0))
        if investment == U128(0):
            raise ContractError.ALREADY_CLAIMED

        # Clear investment to prevent re-entrancy
        self.storage.set(f"prod_invested_{product_id}_{caller}", U128(0))

        # Retrieve settlement percentages
        payoff_bps = self.storage.get(f"prod_settled_payoff_bps_{product_id}", U128(0))
        interest_bps = self.storage.get(f"prod_settled_interest_bps_{product_id}", U128(0))
        
        guarantee_pct = self.storage.get(f"prod_guarantee_{product_id}", U64(0))
        participation_bps = self.storage.get(f"prod_participation_{product_id}", U64(0))

        # 1. Payout guaranteed amount: investment * guarantee_pct / 100
        guaranteed_payout = (investment * U128(guarantee_pct)) / U128(100)

        # 2. Payout yield interest on the guaranteed portion
        interest_payout = (guaranteed_payout * interest_bps) / U128(10000)

        # 3. Payout option payoff upside
        # Option payout = investment * (participation_bps / 10000) * (payoff_bps / 10000)
        option_payout = (investment * U128(participation_bps) * payoff_bps) / U128(100_000_000)

        total_payout = guaranteed_payout + interest_payout + option_payout

        token = self.storage.get("stablecoin")
        contract_addr = self.env.current_contract_address()
        
        # Transfer total payout to investor
        self.env.call(token, "transfer", contract_addr, caller, total_payout)

        self.env.emit_event("claimed", {
            "product_id": product_id,
            "investor": caller,
            "guaranteed": guaranteed_payout,
            "interest": interest_payout,
            "option": option_payout,
            "total": total_payout
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause product creation (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_product_details(self, product_id: U64) -> Map:
        """Query detailed structured product specs and statuses."""
        res = Map(self.env)
        status = self.storage.get(f"prod_status_{product_id}")
        if status is not None:
            res.set("market", self.storage.get(f"prod_market_{product_id}"))
            res.set("strike_price", self.storage.get(f"prod_strike_{product_id}"))
            res.set("deadline", self.storage.get(f"prod_deadline_{product_id}"))
            res.set("maturity", self.storage.get(f"prod_maturity_{product_id}"))
            res.set("participation_bps", self.storage.get(f"prod_participation_{product_id}"))
            res.set("guarantee_pct", self.storage.get(f"prod_guarantee_{product_id}"))
            res.set("status", status)
            res.set("total_invested", self.storage.get(f"prod_total_invested_{product_id}"))
        return res

    @view
    def get_investor_balance(self, product_id: U64, investor: Address) -> U128:
        """Query investment balance of a user in a structured product."""
        return self.storage.get(f"prod_invested_{product_id}_{investor}", U128(0))

    # --- INTERNAL HELPERS ---

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_not_paused(self):
        if self.storage.get("paused", False):
            raise ContractError.PAUSED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _get_now(self) -> U64:
        return self.env.ledger_timestamp()

    def _get_oracle_price(self, market: Symbol) -> U128:
        oracle = self.storage.get("oracle")
        try:
            return self.env.call(oracle, "get_price", market)
        except Exception:
            raise ContractError.ORACLE_READ_FAILED
