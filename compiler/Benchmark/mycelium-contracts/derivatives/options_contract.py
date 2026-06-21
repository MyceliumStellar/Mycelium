"""
Options Contract — American and European call/put options with oracle cash settlement.

Mycelium Smart Contract for Stellar. Enables writers to deposit collateral and write 
call/put options. Buyers purchase options by paying premiums to the writer.
Options are exercised within specific windows, utilizing oracle prices for cash settlement, 
or refunded to writers upon expiration.
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
    OPTION_NOT_FOUND = 5
    OPTION_ALREADY_BOUGHT = 6
    OPTION_NOT_ACTIVE = 7
    EXPIRED = 8
    NOT_EXPIRED = 9
    NOT_EXERCISE_WINDOW = 10
    INSUFFICIENT_COLLATERAL = 11
    ORACLE_READ_FAILED = 12
    INVALID_STYLE = 13

# Option Style
STYLE_AMERICAN = U64(0)
STYLE_EUROPEAN = U64(1)

# Option Status
STATUS_WRITTEN = U64(0)
STATUS_ACTIVE = U64(1)
STATUS_EXERCISED = U64(2)
STATUS_EXPIRED = U64(3)

@contract
class OptionsContract:
    """
    DeFi options contract supporting American/European Call/Put styles.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        oracle: Address,
        collateral_token: Address,
        exercise_window: U64 # Duration (seconds) of European exercise window
    ):
        """Initialize configurations, stablecoin/collateral token, and oracle."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("oracle", oracle)
        self.storage.set("collateral_token", collateral_token)
        self.storage.set("exercise_window", exercise_window)
        self.storage.set("option_next_id", U64(1))
        
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "oracle": oracle,
            "collateral_token": collateral_token,
            "exercise_window": exercise_window
        })

    @external
    def write_option(
        self,
        caller: Address,
        market: Symbol,
        strike_price: U128,
        expiry: U64,
        premium: U128,
        style: U64,      # 0 = American, 1 = European
        is_call: Bool,
        collateral_amount: U128
    ) -> U64:
        """
        Create (write) a new option. Writer deposits stablecoin collateral to back the payout.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if style != STYLE_AMERICAN and style != STYLE_EUROPEAN:
            raise ContractError.INVALID_STYLE

        if expiry <= self._get_now():
            raise ContractError.EXPIRED

        if collateral_amount == U128(0):
            raise ContractError.INSUFFICIENT_COLLATERAL

        # Transfer collateral to contract
        token = self.storage.get("collateral_token")
        contract_addr = self.env.current_contract_address()
        self.env.call(token, "transfer", caller, contract_addr, collateral_amount)

        option_id = self.storage.get("option_next_id", U64(1))
        self.storage.set("option_next_id", option_id + U64(1))

        # Store option parameters
        self.storage.set(f"opt_writer_{option_id}", caller)
        self.storage.set(f"opt_buyer_{option_id}", Address(caller)) # default to writer until bought
        self.storage.set(f"opt_market_{option_id}", market)
        self.storage.set(f"opt_strike_{option_id}", strike_price)
        self.storage.set(f"opt_expiry_{option_id}", expiry)
        self.storage.set(f"opt_premium_{option_id}", premium)
        self.storage.set(f"opt_style_{option_id}", style)
        self.storage.set(f"opt_is_call_{option_id}", is_call)
        self.storage.set(f"opt_collateral_{option_id}", collateral_amount)
        self.storage.set(f"opt_status_{option_id}", STATUS_WRITTEN)

        self.env.emit_event("option_written", {
            "option_id": option_id,
            "writer": caller,
            "market": market,
            "strike_price": strike_price,
            "expiry": expiry,
            "premium": premium,
            "is_call": is_call,
            "collateral": collateral_amount
        })

        return option_id

    @external
    def buy_option(self, caller: Address, option_id: U64):
        """
        Purchase a written option by paying the premium to the writer.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        status = self.storage.get(f"opt_status_{option_id}")
        if status is None:
            raise ContractError.OPTION_NOT_FOUND
        if status != STATUS_WRITTEN:
            raise ContractError.OPTION_ALREADY_BOUGHT

        writer = self.storage.get(f"opt_writer_{option_id}")
        premium = self.storage.get(f"opt_premium_{option_id}", U128(0))

        # Transfer premium from buyer to writer
        token = self.storage.get("collateral_token")
        if premium > U128(0):
            self.env.call(token, "transfer", caller, writer, premium)

        # Update status and buyer
        self.storage.set(f"opt_buyer_{option_id}", caller)
        self.storage.set(f"opt_status_{option_id}", STATUS_ACTIVE)

        self.env.emit_event("option_bought", {
            "option_id": option_id,
            "buyer": caller
        })

    @external
    def exercise_option(self, caller: Address, option_id: U64):
        """
        Exercise an active option (buyer only).
        Cash settles the option profit based on oracle spot price.
        """
        caller.require_auth()
        self._require_initialized()

        status = self.storage.get(f"opt_status_{option_id}")
        if status is None:
            raise ContractError.OPTION_NOT_FOUND
        if status != STATUS_ACTIVE:
            raise ContractError.OPTION_NOT_ACTIVE

        buyer = self.storage.get(f"opt_buyer_{option_id}")
        if caller != buyer:
            raise ContractError.UNAUTHORIZED

        expiry = self.storage.get(f"opt_expiry_{option_id}", U64(0))
        style = self.storage.get(f"opt_style_{option_id}", STYLE_AMERICAN)
        now = self._get_now()

        # Check exercise window based on style
        if style == STYLE_AMERICAN:
            if now > expiry:
                raise ContractError.EXPIRED
        else: # STYLE_EUROPEAN
            window = self.storage.get("exercise_window", U64(0))
            if now < expiry or now > expiry + window:
                raise ContractError.NOT_EXERCISE_WINDOW

        # Retrieve contract variables
        market = self.storage.get(f"opt_market_{option_id}")
        strike = self.storage.get(f"opt_strike_{option_id}", U128(0))
        collateral = self.storage.get(f"opt_collateral_{option_id}", U128(0))
        is_call = self.storage.get(f"opt_is_call_{option_id}", False)
        writer = self.storage.get(f"opt_writer_{option_id}")

        # Fetch current spot price from oracle
        spot = self._get_oracle_price(market)

        # Calculate profit in stablecoin
        # Profit per unit = Spot - Strike (Call) or Strike - Spot (Put)
        # Total profit = unit_profit * collateral_size (here we assume unit amount is equivalent to collateral size)
        # Cash settlement caps at the deposited collateral size.
        buyer_payout = U128(0)
        
        if is_call:
            if spot > strike:
                # profit = spot - strike
                # buyer gets payout proportional to collateral
                # e.g., payout = (spot - strike) * collateral / spot
                buyer_payout = ((spot - strike) * collateral) / spot
        else: # Put
            if spot < strike:
                # profit = strike - spot
                # payout = (strike - spot) * collateral / strike
                buyer_payout = ((strike - spot) * collateral) / strike

        if buyer_payout > collateral:
            buyer_payout = collateral

        writer_payout = collateral - buyer_payout

        # Update status
        self.storage.set(f"opt_status_{option_id}", STATUS_EXERCISED)

        # Disburse tokens
        token = self.storage.get("collateral_token")
        contract_addr = self.env.current_contract_address()

        if buyer_payout > U128(0):
            self.env.call(token, "transfer", contract_addr, buyer, buyer_payout)

        if writer_payout > U128(0):
            self.env.call(token, "transfer", contract_addr, writer, writer_payout)

        self.env.emit_event("option_exercised", {
            "option_id": option_id,
            "buyer": buyer,
            "spot_price": spot,
            "buyer_payout": buyer_payout,
            "writer_payout": writer_payout
        })

    @external
    def refund_expired_option(self, caller: Address, option_id: U64):
        """
        Refund all locked collateral back to the writer after option has expired without exercise.
        """
        self._require_initialized()

        status = self.storage.get(f"opt_status_{option_id}")
        if status is None:
            raise ContractError.OPTION_NOT_FOUND

        # Option can be refunded if it is still written (not bought) or active (bought but expired)
        if status != STATUS_WRITTEN and status != STATUS_ACTIVE:
            raise ContractError.OPTION_NOT_ACTIVE

        expiry = self.storage.get(f"opt_expiry_{option_id}", U64(0))
        style = self.storage.get(f"opt_style_{option_id}", STYLE_AMERICAN)
        now = self._get_now()

        # Validate expiration has passed
        if style == STYLE_AMERICAN:
            if now <= expiry:
                raise ContractError.NOT_EXPIRED
        else: # European
            window = self.storage.get("exercise_window", U64(0))
            if now <= expiry + window:
                raise ContractError.NOT_EXPIRED

        writer = self.storage.get(f"opt_writer_{option_id}")
        collateral = self.storage.get(f"opt_collateral_{option_id}", U128(0))

        # Set status
        self.storage.set(f"opt_status_{option_id}", STATUS_EXPIRED)

        # Return all collateral to the writer
        token = self.storage.get("collateral_token")
        contract_addr = self.env.current_contract_address()
        self.env.call(token, "transfer", contract_addr, writer, collateral)

        self.env.emit_event("option_refunded", {
            "option_id": option_id,
            "writer": writer,
            "collateral": collateral
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause option writing (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_option_details(self, option_id: U64) -> Map:
        """Query parameters of a specific option."""
        res = Map(self.env)
        status = self.storage.get(f"opt_status_{option_id}")
        if status is not None:
            res.set("writer", self.storage.get(f"opt_writer_{option_id}"))
            res.set("buyer", self.storage.get(f"opt_buyer_{option_id}"))
            res.set("market", self.storage.get(f"opt_market_{option_id}"))
            res.set("strike_price", self.storage.get(f"opt_strike_{option_id}"))
            res.set("expiry", self.storage.get(f"opt_expiry_{option_id}"))
            res.set("premium", self.storage.get(f"opt_premium_{option_id}"))
            res.set("style", self.storage.get(f"opt_style_{option_id}"))
            res.set("is_call", self.storage.get(f"opt_is_call_{option_id}"))
            res.set("collateral", self.storage.get(f"opt_collateral_{option_id}"))
            res.set("status", status)
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

    def _get_oracle_price(self, market: Symbol) -> U128:
        """Call price feed oracle to fetch spot price."""
        oracle = self.storage.get("oracle")
        try:
            return self.env.call(oracle, "get_price", market)
        except Exception:
            raise ContractError.ORACLE_READ_FAILED
