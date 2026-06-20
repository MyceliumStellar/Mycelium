"""
Forward Contract — Bilateral forward execution, collateral maintenance, and physical or cash delivery rules.

Mycelium Smart Contract for Stellar. Facilitates custom OTC forward contracts between a buyer and seller.
Supports cash settlement based on oracle price at maturity, as well as physical settlement (asset-for-cash swap).
Monitors collateral margins based on price movements and handles defaults if margin maintenance is breached.
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
    CONTRACT_NOT_FOUND = 6
    CONTRACT_NOT_ACTIVE = 7
    NOT_MATURED = 8
    ALREADY_MATURED = 9
    INSUFFICIENT_MARGIN = 10
    ORACLE_READ_FAILED = 11
    SETTLEMENT_FAILED = 12
    PHYSICAL_STEP_ERROR = 13

@contract
class ForwardContract:
    """
    Bilateral OTC Forward Contract.
    Delivery Type:
    - 0: Cash settlement (collateral transfer representing price differences)
    - 1: Physical delivery (actual underlying token swap against stablecoin)
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
        maintenance_margin_ratio_bps: U64 # e.g. 5000 for 50% of initial margin
    ):
        """Initialize configurations."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("oracle", oracle)
        self.storage.set("collateral_token", collateral_token)
        self.storage.set("maintenance_ratio", maintenance_margin_ratio_bps)
        self.storage.set("forward_nonce", U64(1))
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "oracle": oracle,
            "collateral_token": collateral_token
        })

    @external
    def create_forward_contract(
        self,
        caller: Address,
        seller: Address,
        underlying_asset: Symbol, # Symbol for price tracking, e.g. "XLM"
        underlying_token: Address, # Contract address of underlying if physical delivery
        asset_amount: U128,
        forward_price: U128,       # Agreed price per unit
        delivery_time: U64,
        delivery_type: U64,        # 0 = Cash, 1 = Physical
        initial_margin: U128
    ) -> U64:
        """Buyer creates a forward contract and locks initial margin."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        now = self._get_now()
        if delivery_time <= now or asset_amount == U128(0) or forward_price == U128(0):
            raise ContractError.INVALID_PARAM

        if delivery_type != U64(0) and delivery_type != U64(1):
            raise ContractError.INVALID_PARAM

        # Deposit initial margin from buyer
        collateral = self.storage.get("collateral_token")
        contract_addr = self.env.current_contract_address()
        self.env.call(collateral, "transfer", caller, contract_addr, initial_margin)

        forward_id = self.storage.get("forward_nonce", U64(1))
        self.storage.set("forward_nonce", forward_id + U64(1))

        # Store contract details
        prefix = f"fwd_{forward_id}_"
        self.storage.set(prefix + "buyer", caller)
        self.storage.set(prefix + "seller", seller)
        self.storage.set(prefix + "asset_symbol", underlying_asset)
        self.storage.set(prefix + "asset_token", underlying_token)
        self.storage.set(prefix + "asset_amount", asset_amount)
        self.storage.set(prefix + "forward_price", forward_price)
        self.storage.set(prefix + "delivery_time", delivery_time)
        self.storage.set(prefix + "delivery_type", delivery_type)
        self.storage.set(prefix + "buyer_margin", initial_margin)
        self.storage.set(prefix + "seller_margin", U128(0)) # Must be funded by seller
        self.storage.set(prefix + "initial_margin_req", initial_margin)
        self.storage.set(prefix + "status", Symbol("PENDING"))
        
        # Physical settlement progression trackers
        self.storage.set(prefix + "seller_deposited_asset", False)
        self.storage.set(prefix + "buyer_deposited_cash", False)

        self.env.emit_event("forward_created", {
            "forward_id": forward_id,
            "buyer": caller,
            "seller": seller,
            "forward_price": forward_price,
            "delivery_time": delivery_time
        })

        return forward_id

    @external
    def accept_forward_contract(self, caller: Address, forward_id: U64, initial_margin: U128):
        """Seller accepts the forward offer and deposits initial margin."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        prefix = f"fwd_{forward_id}_"
        seller = self.storage.get(prefix + "seller")
        if seller is None:
            raise ContractError.CONTRACT_NOT_FOUND

        if caller != seller:
            raise ContractError.UNAUTHORIZED

        status = self.storage.get(prefix + "status")
        if status != Symbol("PENDING"):
            raise ContractError.INVALID_PARAM

        # Deposit margin
        collateral = self.storage.get("collateral_token")
        contract_addr = self.env.current_contract_address()
        self.env.call(collateral, "transfer", caller, contract_addr, initial_margin)

        self.storage.set(prefix + "seller_margin", initial_margin)
        self.storage.set(prefix + "status", Symbol("ACTIVE"))

        self.env.emit_event("forward_activated", {
            "forward_id": forward_id,
            "seller": caller,
            "margin": initial_margin
        })

    @external
    def deposit_margin(self, caller: Address, forward_id: U64, amount: U128):
        """Add margin buffer to contract."""
        caller.require_auth()
        self._require_initialized()

        prefix = f"fwd_{forward_id}_"
        buyer = self.storage.get(prefix + "buyer")
        seller = self.storage.get(prefix + "seller")

        if buyer is None:
            raise ContractError.CONTRACT_NOT_FOUND

        collateral = self.storage.get("collateral_token")
        contract_addr = self.env.current_contract_address()
        self.env.call(collateral, "transfer", caller, contract_addr, amount)

        if caller == buyer:
            margin = self.storage.get(prefix + "buyer_margin", U128(0))
            self.storage.set(prefix + "buyer_margin", margin + amount)
        elif caller == seller:
            margin = self.storage.get(prefix + "seller_margin", U128(0))
            self.storage.set(prefix + "seller_margin", margin + amount)
        else:
            raise ContractError.UNAUTHORIZED

        self.env.emit_event("margin_deposited", {
            "forward_id": forward_id,
            "depositor": caller,
            "amount": amount
        })

    @external
    def check_margin_maintenance(self, forward_id: U64):
        """
        Check that both parties meet maintenance margin requirements.
        If current asset price shifts, PnL is applied to margins.
        If a party has insufficient margin, their position is liquidated/defaulted.
        """
        self._require_initialized()

        prefix = f"fwd_{forward_id}_"
        status = self.storage.get(prefix + "status")
        if status != Symbol("ACTIVE"):
            raise ContractError.CONTRACT_NOT_ACTIVE

        # Fetch current price from oracle
        symbol = self.storage.get(prefix + "asset_symbol")
        current_price = self._get_oracle_price(symbol)

        fwd_price = self.storage.get(prefix + "forward_price", U128(0))
        amount = self.storage.get(prefix + "asset_amount", U128(0))

        buyer = self.storage.get(prefix + "buyer")
        seller = self.storage.get(prefix + "seller")
        buyer_margin = self.storage.get(prefix + "buyer_margin", U128(0))
        seller_margin = self.storage.get(prefix + "seller_margin", U128(0))
        initial_margin_req = self.storage.get(prefix + "initial_margin_req", U128(0))
        maintenance_ratio = self.storage.get("maintenance_ratio", U64(0))

        # Valuation difference = amount * (current_price - forward_price)
        # Buyer is long, seller is short.
        price_diff = I128(int(current_price)) - I128(int(fwd_price))
        pnl = (I128(int(amount)) * price_diff) / I128(10_000_000) # scale depending on decimals

        buyer_value = I128(int(buyer_margin)) + pnl
        seller_value = I128(int(seller_margin)) - pnl

        maintenance_limit = (initial_margin_req * U128(maintenance_ratio)) / U128(10000)

        # Trigger liquidation if margin falls below maintenance limit
        if buyer_value < I128(int(maintenance_limit)):
            # Buyer defaults. All margins to seller.
            self._trigger_default(forward_id, Symbol("BUYER_DEFAULT"))
        elif seller_value < I128(int(maintenance_limit)):
            # Seller defaults. All margins to buyer.
            self._trigger_default(forward_id, Symbol("SELLER_DEFAULT"))

    @external
    def execute_cash_settlement(self, forward_id: U64):
        """Cash settlement at maturity. Settles net difference between forward price and oracle price."""
        self._require_initialized()
        self._require_not_paused()

        prefix = f"fwd_{forward_id}_"
        status = self.storage.get(prefix + "status")
        if status != Symbol("ACTIVE"):
            raise ContractError.CONTRACT_NOT_ACTIVE

        delivery_time = self.storage.get(prefix + "delivery_time", U64(0))
        if self._get_now() < delivery_time:
            raise ContractError.NOT_MATURED

        delivery_type = self.storage.get(prefix + "delivery_type", U64(0))
        if delivery_type != U64(0):
            raise ContractError.PHYSICAL_STEP_ERROR

        symbol = self.storage.get(prefix + "asset_symbol")
        current_price = self._get_oracle_price(symbol)

        fwd_price = self.storage.get(prefix + "forward_price", U128(0))
        amount = self.storage.get(prefix + "asset_amount", U128(0))

        buyer = self.storage.get(prefix + "buyer")
        seller = self.storage.get(prefix + "seller")
        buyer_margin = self.storage.get(prefix + "buyer_margin", U128(0))
        seller_margin = self.storage.get(prefix + "seller_margin", U128(0))

        # Valuation difference = amount * (current_price - forward_price)
        price_diff = I128(int(current_price)) - I128(int(fwd_price))
        pnl = (I128(int(amount)) * price_diff) / I128(10_000_000)

        buyer_payout = I128(int(buyer_margin)) + pnl
        seller_payout = I128(int(seller_margin)) - pnl

        # Ensure no negative payouts (max payout capped at total pool)
        total_collateral = buyer_margin + seller_margin
        
        if buyer_payout < I128(0):
            b_pay = U128(0)
            s_pay = total_collateral
        elif seller_payout < I128(0):
            s_pay = U128(0)
            b_pay = total_collateral
        else:
            b_pay = U128(int(buyer_payout))
            s_pay = U128(int(seller_payout))

        self.storage.set(prefix + "status", Symbol("SETTLED"))
        self.storage.set(prefix + "buyer_margin", U128(0))
        self.storage.set(prefix + "seller_margin", U128(0))

        collateral = self.storage.get("collateral_token")
        contract_addr = self.env.current_contract_address()

        if b_pay > U128(0):
            self.env.call(collateral, "transfer", contract_addr, buyer, b_pay)
        if s_pay > U128(0):
            self.env.call(collateral, "transfer", contract_addr, seller, s_pay)

        self.env.emit_event("forward_settled_cash", {
            "forward_id": forward_id,
            "final_price": current_price,
            "buyer_payout": b_pay,
            "seller_payout": s_pay
        })

    @external
    def seller_deposit_physical(self, caller: Address, forward_id: U64):
        """Physical Settlement step 1: Seller deposits underlying asset to contract."""
        caller.require_auth()
        self._require_initialized()

        prefix = f"fwd_{forward_id}_"
        status = self.storage.get(prefix + "status")
        if status != Symbol("ACTIVE"):
            raise ContractError.CONTRACT_NOT_ACTIVE

        if self._get_now() < self.storage.get(prefix + "delivery_time", U64(0)):
            raise ContractError.NOT_MATURED

        if self.storage.get(prefix + "delivery_type", U64(0)) != U64(1):
            raise ContractError.PHYSICAL_STEP_ERROR

        seller = self.storage.get(prefix + "seller")
        if caller != seller:
            raise ContractError.UNAUTHORIZED

        if self.storage.get(prefix + "seller_deposited_asset", False):
            raise ContractError.PHYSICAL_STEP_ERROR

        asset_token = self.storage.get(prefix + "asset_token")
        asset_amount = self.storage.get(prefix + "asset_amount", U128(0))
        contract_addr = self.env.current_contract_address()

        # Transfer underlying from seller to contract
        self.env.call(asset_token, "transfer", seller, contract_addr, asset_amount)
        self.storage.set(prefix + "seller_deposited_asset", True)

        self.env.emit_event("seller_deposited_asset", {
            "forward_id": forward_id,
            "amount": asset_amount
        })

        self._check_and_execute_physical_swap(forward_id)

    @external
    def buyer_deposit_physical(self, caller: Address, forward_id: U64):
        """Physical Settlement step 2: Buyer deposits cash stablecoin payment."""
        caller.require_auth()
        self._require_initialized()

        prefix = f"fwd_{forward_id}_"
        status = self.storage.get(prefix + "status")
        if status != Symbol("ACTIVE"):
            raise ContractError.CONTRACT_NOT_ACTIVE

        if self._get_now() < self.storage.get(prefix + "delivery_time", U64(0)):
            raise ContractError.NOT_MATURED

        if self.storage.get(prefix + "delivery_type", U64(0)) != U64(1):
            raise ContractError.PHYSICAL_STEP_ERROR

        buyer = self.storage.get(prefix + "buyer")
        if caller != buyer:
            raise ContractError.UNAUTHORIZED

        if self.storage.get(prefix + "buyer_deposited_cash", False):
            raise ContractError.PHYSICAL_STEP_ERROR

        # Cash required = asset_amount * forward_price
        asset_amount = self.storage.get(prefix + "asset_amount", U128(0))
        forward_price = self.storage.get(prefix + "forward_price", U128(0))
        cash_required = asset_amount * forward_price / U128(10_000_000)

        collateral = self.storage.get("collateral_token")
        contract_addr = self.env.current_contract_address()

        # Transfer payment from buyer to contract
        self.env.call(collateral, "transfer", buyer, contract_addr, cash_required)
        self.storage.set(prefix + "buyer_deposited_cash", True)

        self.env.emit_event("buyer_deposited_cash", {
            "forward_id": forward_id,
            "amount": cash_required
        })

        self._check_and_execute_physical_swap(forward_id)

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause forward execution (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_forward_details(self, forward_id: U64) -> Map:
        """Query details of a forward agreement."""
        res = Map(self.env)
        prefix = f"fwd_{forward_id}_"
        buyer = self.storage.get(prefix + "buyer")
        if buyer is not None:
            res.set("buyer", buyer)
            res.set("seller", self.storage.get(prefix + "seller"))
            res.set("asset_symbol", self.storage.get(prefix + "asset_symbol"))
            res.set("asset_token", self.storage.get(prefix + "asset_token"))
            res.set("asset_amount", self.storage.get(prefix + "asset_amount"))
            res.set("forward_price", self.storage.get(prefix + "forward_price"))
            res.set("delivery_time", self.storage.get(prefix + "delivery_time"))
            res.set("delivery_type", self.storage.get(prefix + "delivery_type"))
            res.set("buyer_margin", self.storage.get(prefix + "buyer_margin"))
            res.set("seller_margin", self.storage.get(prefix + "seller_margin"))
            res.set("status", self.storage.get(prefix + "status"))
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
        """Call external Oracle to fetch current asset price."""
        oracle = self.storage.get("oracle")
        try:
            return self.env.call(oracle, "get_price", asset)
        except Exception:
            raise ContractError.ORACLE_READ_FAILED

    def _trigger_default(self, forward_id: U64, reason: Symbol):
        """In case of margin breach, liquidates the contract and awards all collateral to the counterparty."""
        prefix = f"fwd_{forward_id}_"
        self.storage.set(prefix + "status", Symbol("DEFAULTED"))

        buyer = self.storage.get(prefix + "buyer")
        seller = self.storage.get(prefix + "seller")
        buyer_margin = self.storage.get(prefix + "buyer_margin", U128(0))
        seller_margin = self.storage.get(prefix + "seller_margin", U128(0))
        total_margin = buyer_margin + seller_margin

        self.storage.set(prefix + "buyer_margin", U128(0))
        self.storage.set(prefix + "seller_margin", U128(0))

        collateral = self.storage.get("collateral_token")
        contract_addr = self.env.current_contract_address()

        if reason == Symbol("BUYER_DEFAULT"):
            # All funds to Seller
            if total_margin > U128(0):
                self.env.call(collateral, "transfer", contract_addr, seller, total_margin)
        else:
            # All funds to Buyer
            if total_margin > U128(0):
                self.env.call(collateral, "transfer", contract_addr, buyer, total_margin)

        self.env.emit_event("forward_defaulted", {
            "forward_id": forward_id,
            "liquidated_party": reason,
            "payout": total_margin
        })

    def _check_and_execute_physical_swap(self, forward_id: U64):
        """Check if both parties deposited physical delivery terms and complete swap."""
        prefix = f"fwd_{forward_id}_"
        if not self.storage.get(prefix + "seller_deposited_asset", False):
            return
        if not self.storage.get(prefix + "buyer_deposited_cash", False):
            return

        buyer = self.storage.get(prefix + "buyer")
        seller = self.storage.get(prefix + "seller")
        asset_token = self.storage.get(prefix + "asset_token")
        asset_amount = self.storage.get(prefix + "asset_amount", U128(0))
        forward_price = self.storage.get(prefix + "forward_price", U128(0))

        # Cash payment = asset_amount * forward_price
        cash_payout = asset_amount * forward_price / U128(10_000_000)

        # Retrieve margins to refund
        buyer_margin = self.storage.get(prefix + "buyer_margin", U128(0))
        seller_margin = self.storage.get(prefix + "seller_margin", U128(0))

        self.storage.set(prefix + "status", Symbol("SETTLED"))
        self.storage.set(prefix + "buyer_margin", U128(0))
        self.storage.set(prefix + "seller_margin", U128(0))

        collateral = self.storage.get("collateral_token")
        contract_addr = self.env.current_contract_address()

        # 1. Send underlying asset to Buyer
        self.env.call(asset_token, "transfer", contract_addr, buyer, asset_amount)

        # 2. Send cash payment to Seller
        self.env.call(collateral, "transfer", contract_addr, seller, cash_payout)

        # 3. Refund original margin deposits
        if buyer_margin > U128(0):
            self.env.call(collateral, "transfer", contract_addr, buyer, buyer_margin)
        if seller_margin > U128(0):
            self.env.call(collateral, "transfer", contract_addr, seller, seller_margin)

        self.env.emit_event("forward_settled_physical", {
            "forward_id": forward_id,
            "asset_transferred": asset_amount,
            "cash_transferred": cash_payout
        })
