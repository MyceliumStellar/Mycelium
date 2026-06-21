"""
Variance Swap — Volatility measures, variance calculation, payoff settlements, and margin buffers.

Mycelium Smart Contract for Stellar. Tracks variance swap agreements where the long party receives
the difference between realized variance and strike variance, while the short party pays it.
Price observations are recorded periodically from an oracle, and realized variance is calculated using
log-free returns. Margin collateral buffers are held from both parties and settled upon maturity.
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
    INVALID_PARAM = 5
    SWAP_NOT_FOUND = 6
    SWAP_NOT_ACTIVE = 7
    INTERVAL_NOT_MET = 8
    OBSERVATIONS_NOT_COMPLETE = 9
    OBSERVATIONS_ALREADY_COMPLETE = 10
    INSUFFICIENT_MARGIN = 11
    ORACLE_READ_FAILED = 12

@contract
class VarianceSwap:
    """
    Variance swap contract representing a volatility-based derivative.
    Realized variance is scaled for integer precision.
    Variance Strike and Realized Variance are represented in basis points squared (bps^2).
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        oracle: Address,
        collateral_token: Address
    ):
        """Initialize contract configuration."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("oracle", oracle)
        self.storage.set("collateral_token", collateral_token)
        self.storage.set("swap_nonce", U64(1))
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "oracle": oracle,
            "collateral_token": collateral_token
        })

    @external
    def create_swap(
        self,
        caller: Address,
        counterparty: Address,
        underlying: Symbol,
        strike_variance: U64,      # strike volatility^2, e.g. 400 for 20% vol (20^2)
        cap_variance: U64,         # cap volatility^2, e.g. 1600 for 40% vol (40^2)
        vega_notional: U128,       # payout per unit of variance
        observation_interval: U64, # duration between observations in seconds
        total_observations: U64,   # total number of observation points
        initial_margin: U128
    ) -> U64:
        """
        Create a variance swap offering.
        Caller acts as the Long Variance party (buying volatility).
        Counterparty acts as the Short Variance party (selling volatility).
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if total_observations < U64(2) or strike_variance >= cap_variance or vega_notional == U128(0):
            raise ContractError.INVALID_PARAM

        swap_id = self.storage.get("swap_nonce", U64(1))
        self.storage.set("swap_nonce", swap_id + U64(1))

        # Transfer margin from Long variance buyer
        token = self.storage.get("collateral_token")
        contract_addr = self.env.current_contract_address()
        self.env.call(token, "transfer", caller, contract_addr, initial_margin)

        prefix = f"swap_{swap_id}_"
        self.storage.set(prefix + "long_party", caller)
        self.storage.set(prefix + "short_party", counterparty)
        self.storage.set(prefix + "underlying", underlying)
        self.storage.set(prefix + "strike_variance", strike_variance)
        self.storage.set(prefix + "cap_variance", cap_variance)
        self.storage.set(prefix + "vega_notional", vega_notional)
        self.storage.set(prefix + "obs_interval", observation_interval)
        self.storage.set(prefix + "total_obs", total_observations)
        self.storage.set(prefix + "long_margin", initial_margin)
        self.storage.set(prefix + "short_margin", U128(0)) # Must be funded by counterparty
        self.storage.set(prefix + "status", Symbol("PENDING"))
        
        # Observations storage structures
        self.storage.set(prefix + "obs_count", U64(0))
        self.storage.set(prefix + "last_obs_time", U64(0))
        self.storage.set(prefix + "squared_returns_sum", U128(0))

        self.env.emit_event("swap_created", {
            "swap_id": swap_id,
            "long_party": caller,
            "short_party": counterparty,
            "strike_variance": strike_variance,
            "vega_notional": vega_notional
        })

        return swap_id

    @external
    def accept_swap(self, caller: Address, swap_id: U64, initial_margin: U128):
        """Counterparty accepts the swap and deposits their initial margin."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        prefix = f"swap_{swap_id}_"
        short_party = self.storage.get(prefix + "short_party")
        if short_party is None:
            raise ContractError.SWAP_NOT_FOUND

        if caller != short_party:
            raise ContractError.UNAUTHORIZED

        status = self.storage.get(prefix + "status")
        if status != Symbol("PENDING"):
            raise ContractError.INVALID_PARAM

        # Deposit margin
        token = self.storage.get("collateral_token")
        contract_addr = self.env.current_contract_address()
        self.env.call(token, "transfer", caller, contract_addr, initial_margin)

        self.storage.set(prefix + "short_margin", initial_margin)
        self.storage.set(prefix + "status", Symbol("ACTIVE"))

        self.env.emit_event("swap_activated", {
            "swap_id": swap_id,
            "short_party": caller,
            "margin_deposited": initial_margin
        })

    @external
    def deposit_margin(self, caller: Address, swap_id: U64, amount: U128):
        """Allow either party to add extra margin buffer to their position."""
        caller.require_auth()
        self._require_initialized()

        prefix = f"swap_{swap_id}_"
        long_party = self.storage.get(prefix + "long_party")
        short_party = self.storage.get(prefix + "short_party")

        if long_party is None:
            raise ContractError.SWAP_NOT_FOUND

        token = self.storage.get("collateral_token")
        contract_addr = self.env.current_contract_address()
        self.env.call(token, "transfer", caller, contract_addr, amount)

        if caller == long_party:
            margin = self.storage.get(prefix + "long_margin", U128(0))
            self.storage.set(prefix + "long_margin", margin + amount)
        elif caller == short_party:
            margin = self.storage.get(prefix + "short_margin", U128(0))
            self.storage.set(prefix + "short_margin", margin + amount)
        else:
            raise ContractError.UNAUTHORIZED

        self.env.emit_event("margin_deposited", {
            "swap_id": swap_id,
            "depositor": caller,
            "amount": amount
        })

    @external
    def record_observation(self, swap_id: U64):
        """
        Record a price observation from the oracle.
        Can be called by anyone as long as the observation interval has elapsed.
        """
        self._require_initialized()
        self._require_not_paused()

        prefix = f"swap_{swap_id}_"
        status = self.storage.get(prefix + "status")
        if status != Symbol("ACTIVE"):
            raise ContractError.SWAP_NOT_ACTIVE

        obs_count = self.storage.get(prefix + "obs_count", U64(0))
        total_obs = self.storage.get(prefix + "total_obs", U64(0))

        if obs_count >= total_obs:
            raise ContractError.OBSERVATIONS_ALREADY_COMPLETE

        now = self._get_now()
        last_obs_time = self.storage.get(prefix + "last_obs_time", U64(0))
        interval = self.storage.get(prefix + "obs_interval", U64(0))

        if obs_count > U64(0) and now < last_obs_time + interval:
            raise ContractError.INTERVAL_NOT_MET

        # Fetch oracle price
        underlying = self.storage.get(prefix + "underlying")
        price = self._get_oracle_price(underlying)

        # Store price
        self.storage.set(prefix + f"obs_price_{obs_count}", price)
        self.storage.set(prefix + "last_obs_time", now)
        
        # Calculate return if not the first observation
        if obs_count > U64(0):
            prev_price = self.storage.get(prefix + f"obs_price_{obs_count - U64(1)}", U128(0))
            # Return R_t = (Price_t - Price_{t-1}) / Price_{t-1}
            # Scale returns by 10^5 (e.g. 0.02 return = 2000 return scaled)
            # Return squared is scaled by 10^10
            diff = I128(int(price)) - I128(int(prev_price))
            scaled_ret = (diff * I128(100_000)) / I128(int(prev_price))
            ret_squared = U128(int(scaled_ret * scaled_ret))

            squared_sum = self.storage.get(prefix + "squared_returns_sum", U128(0))
            self.storage.set(prefix + "squared_returns_sum", squared_sum + ret_squared)

        # Update obs count
        new_obs_count = obs_count + U64(1)
        self.storage.set(prefix + "obs_count", new_obs_count)

        self.env.emit_event("observation_recorded", {
            "swap_id": swap_id,
            "obs_index": obs_count,
            "price": price,
            "time": now
        })

    @external
    def settle_swap(self, swap_id: U64):
        """
        Settle the variance swap once all observation points are completed.
        Payoff = Vega Notional * (Realized Variance - Strike Variance).
        Realized Variance RV = 252 * Sum(R_t^2) / (N - 1).
        """
        self._require_initialized()
        self._require_not_paused()

        prefix = f"swap_{swap_id}_"
        status = self.storage.get(prefix + "status")
        if status != Symbol("ACTIVE"):
            raise ContractError.SWAP_NOT_ACTIVE

        obs_count = self.storage.get(prefix + "obs_count", U64(0))
        total_obs = self.storage.get(prefix + "total_obs", U64(0))

        if obs_count < total_obs:
            raise ContractError.OBSERVATIONS_NOT_COMPLETE

        # Retrieve inputs
        squared_sum = self.storage.get(prefix + "squared_returns_sum", U128(0))
        strike_variance = self.storage.get(prefix + "strike_variance", U64(0))
        cap_variance = self.storage.get(prefix + "cap_variance", U64(0))
        vega_notional = self.storage.get(prefix + "vega_notional", U128(0))

        # Realized variance RV calculation:
        # Sum is scaled by 10^10.
        # Annualized Realized Variance = 252 * Sum(R_t^2) / (N - 1)
        # RV value in bps^2 = RV * 10^8 (e.g. 0.04 variance is 4% variance or 400 volatility points squared)
        # Let's adjust scale. R_t^2 is scaled by 10^10.
        # RV_bps = (252 * squared_sum) / (100 * (N - 1))
        denominator = U128(100) * U128(int(total_obs - U64(1)))
        realized_variance = (U128(252) * squared_sum) / denominator

        # Realized variance is capped at cap_variance
        rv_scaled = U64(int(realized_variance))
        if rv_scaled > cap_variance:
            rv_scaled = cap_variance

        # Payout calculation:
        # Payoff = Vega Notional * (RV_bps - Strike_bps)
        # Payoff is positive: Long party receives from Short party
        # Payoff is negative: Short party receives from Long party
        is_long_payoff = True
        variance_diff = I128(0)

        if rv_scaled >= strike_variance:
            variance_diff = I128(int(rv_scaled - strike_variance))
            is_long_payoff = True
        else:
            variance_diff = I128(int(strike_variance - rv_scaled))
            is_long_payoff = False

        # Payoff amount
        payoff = vega_notional * U128(int(variance_diff))

        # Check margins
        long_margin = self.storage.get(prefix + "long_margin", U128(0))
        short_margin = self.storage.get(prefix + "short_margin", U128(0))

        long_payout = U128(0)
        short_payout = U128(0)

        if is_long_payoff:
            # Long party wins. Payoff deducted from Short party's margin and sent to Long
            if short_margin < payoff:
                # Default by short party, long party gets everything
                long_payout = long_margin + short_margin
                short_payout = U128(0)
            else:
                long_payout = long_margin + payoff
                short_payout = short_margin - payoff
        else:
            # Short party wins. Payoff deducted from Long party's margin and sent to Short
            if long_margin < payoff:
                # Default by long party, short party gets everything
                short_payout = long_margin + short_margin
                long_payout = U128(0)
            else:
                short_payout = short_margin + payoff
                long_payout = long_margin - payoff

        # Clean storage
        self.storage.set(prefix + "status", Symbol("SETTLED"))
        self.storage.set(prefix + "realized_variance", rv_scaled)
        self.storage.set(prefix + "long_margin", U128(0))
        self.storage.set(prefix + "short_margin", U128(0))

        # Transfer payouts
        token = self.storage.get("collateral_token")
        contract_addr = self.env.current_contract_address()
        long_party = self.storage.get(prefix + "long_party")
        short_party = self.storage.get(prefix + "short_party")

        if long_payout > U128(0):
            self.env.call(token, "transfer", contract_addr, long_party, long_payout)
        if short_payout > U128(0):
            self.env.call(token, "transfer", contract_addr, short_party, short_payout)

        self.env.emit_event("swap_settled", {
            "swap_id": swap_id,
            "realized_variance": rv_scaled,
            "payoff": payoff,
            "is_long_payoff": is_long_payoff
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause contract operations (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_swap_details(self, swap_id: U64) -> Map:
        """Query swap details and parameters."""
        res = Map(self.env)
        prefix = f"swap_{swap_id}_"
        long_party = self.storage.get(prefix + "long_party")
        if long_party is not None:
            res.set("long_party", long_party)
            res.set("short_party", self.storage.get(prefix + "short_party"))
            res.set("underlying", self.storage.get(prefix + "underlying"))
            res.set("strike_variance", self.storage.get(prefix + "strike_variance"))
            res.set("cap_variance", self.storage.get(prefix + "cap_variance"))
            res.set("vega_notional", self.storage.get(prefix + "vega_notional"))
            res.set("obs_count", self.storage.get(prefix + "obs_count"))
            res.set("total_obs", self.storage.get(prefix + "total_obs"))
            res.set("long_margin", self.storage.get(prefix + "long_margin"))
            res.set("short_margin", self.storage.get(prefix + "short_margin"))
            res.set("status", self.storage.get(prefix + "status"))
            res.set("squared_returns_sum", self.storage.get(prefix + "squared_returns_sum"))
            res.set("realized_variance", self.storage.get(prefix + "realized_variance"))
        return res

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

    def _get_oracle_price(self, asset: Symbol) -> U128:
        """Call external Oracle to fetch price."""
        oracle = self.storage.get("oracle")
        try:
            return self.env.call(oracle, "get_price", asset)
        except Exception:
            raise ContractError.ORACLE_READ_FAILED
