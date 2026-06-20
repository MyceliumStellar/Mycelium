"""
Dark Pool Matching Engine — Secret order books, range/price threshold matching verification, and execution locks.

Mycelium Smart Contract for Stellar
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    TRANSFER_FAILED = 4
    ORDER_NOT_FOUND = 5
    ORDER_LOCKED = 6
    ORDER_FILLED = 7
    INVALID_COMMITMENT = 8
    PRICE_OUT_OF_RANGE = 9
    AMOUNT_OUT_OF_RANGE = 10
    COLLATERAL_MISMATCH = 11
    INVALID_ORDER_TYPES = 12


class OrderType:
    BUY = 1
    SELL = 2


@contract
class DarkPoolSystem:
    """Manages anonymous trade commitments, collateral custody, match checks, and execution locking."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        base_token: Address,
        quote_token: Address
    ):
        """Initialize the Dark Pool contract."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("base_token", base_token)
        self.storage.set("quote_token", quote_token)
        self.storage.set("order_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "base_token": base_token,
            "quote_token": quote_token
        })

    # ------------------------------------------------------------------ #
    #  Admin & Operator Configuration                                    #
    # ------------------------------------------------------------------ #

    @external
    def set_matching_engine(self, admin: Address, engine: Address, status: Bool):
        """Register or revoke an authorized off-chain matching engine actor. Only Admin."""
        self._require_admin(admin)
        self.storage.set(("engine", engine), status)
        self.env.emit_event("engine_status_updated", {"engine": engine, "status": status})

    # ------------------------------------------------------------------ #
    #  Trader Operations                                                 #
    # ------------------------------------------------------------------ #

    @external
    def submit_order(
        self,
        trader: Address,
        commitment: Bytes,
        collateral_token: Address,
        collateral_amount: U128
    ) -> U64:
        """Lock collateral tokens and record the secret order commitment.

        The commitment is: Keccak256(order_type, min_amount, max_amount, price_threshold, salt).
        Collateral covers the trade capacity (e.g. quote token for BUY, base token for SELL).
        """
        self._require_initialized()
        trader.require_auth()

        # Validate token type (must be either base or quote token)
        base = self.storage.get("base_token")
        quote = self.storage.get("quote_token")
        if collateral_token != base and collateral_token != quote:
            raise ContractError.COLLATERAL_MISMATCH

        # Transfer collateral to contract
        contract_addr = self.env.current_contract_address()
        success = self.env.invoke_contract(collateral_token, "transfer", [trader, contract_addr, collateral_amount])
        if not success:
            raise ContractError.TRANSFER_FAILED

        o_id = self.storage.get("order_count") + U64(1)
        self.storage.set("order_count", o_id)

        order = {
            "id": o_id,
            "trader": trader,
            "commitment": commitment,
            "collateral_amount": collateral_amount,
            "collateral_token": collateral_token,
            "locked": False,
            "filled": False
        }

        self.storage.set(("order", o_id), order)

        self.env.emit_event("order_submitted", {
            "order_id": o_id,
            "trader": trader,
            "collateral_amount": collateral_amount
        })

        return o_id

    @external
    def cancel_order(self, trader: Address, order_id: U64):
        """Cancel an unfilled, unlocked secret order and refund collateral. Only Trader."""
        self._require_initialized()
        trader.require_auth()

        o = self.storage.get(("order", order_id), None)
        if o is None:
            raise ContractError.ORDER_NOT_FOUND
        
        if o["trader"] != trader:
            raise ContractError.UNAUTHORIZED
        if o["locked"]:
            raise ContractError.ORDER_LOCKED
        if o["filled"]:
            raise ContractError.ORDER_FILLED

        o["filled"] = True
        self.storage.set(("order", order_id), o)

        # Refund collateral
        token = o["collateral_token"]
        amount = o["collateral_amount"]
        contract_addr = self.env.current_contract_address()
        
        success = self.env.invoke_contract(token, "transfer", [contract_addr, trader, amount])
        if not success:
            raise ContractError.TRANSFER_FAILED

        self.env.emit_event("order_cancelled", {"order_id": order_id, "trader": trader})

    # ------------------------------------------------------------------ #
    #  Matching Engine Operations                                        #
    # ------------------------------------------------------------------ #

    @external
    def execute_match(
        self,
        engine: Address,
        buy_order_id: U64,
        sell_order_id: U64,
        # Buy order cleartext params
        buy_min_amount: U128,
        buy_max_amount: U128,
        buy_price_threshold: U128,
        buy_salt: Bytes,
        # Sell order cleartext params
        sell_min_amount: U128,
        sell_max_amount: U128,
        sell_price_threshold: U128,
        sell_salt: Bytes,
        # Execution terms
        match_amount: U128,
        match_price: U128
    ) -> Bool:
        """Verify secret order parameters and execute token transfer matching. Authorized engines only."""
        self._require_initialized()
        engine.require_auth()
        self._require_engine(engine)

        buy_order = self.storage.get(("order", buy_order_id), None)
        sell_order = self.storage.get(("order", sell_order_id), None)

        if buy_order is None or sell_order is None:
            raise ContractError.ORDER_NOT_FOUND

        # Reentrancy / Double-match locks check
        if buy_order["locked"] or sell_order["locked"]:
            raise ContractError.ORDER_LOCKED
        if buy_order["filled"] or sell_order["filled"]:
            raise ContractError.ORDER_FILLED

        # Lock orders during execution
        buy_order["locked"] = True
        sell_order["locked"] = True
        self.storage.set(("order", buy_order_id), buy_order)
        self.storage.set(("order", sell_order_id), sell_order)

        # 1. Cryptographic commitment verification
        # Hash check for buy order: type=BUY
        expected_buy_hash = self.env.crypto().keccak256(U64(OrderType.BUY), buy_min_amount, buy_max_amount, buy_price_threshold, buy_salt)
        if expected_buy_hash != buy_order["commitment"]:
            raise ContractError.INVALID_COMMITMENT

        # Hash check for sell order: type=SELL
        expected_sell_hash = self.env.crypto().keccak256(U64(OrderType.SELL), sell_min_amount, sell_max_amount, sell_price_threshold, sell_salt)
        if expected_sell_hash != sell_order["commitment"]:
            raise ContractError.INVALID_COMMITMENT

        # 2. Check Match parameters compatibility
        # Check price thresholds
        if match_price > buy_price_threshold or match_price < sell_price_threshold:
            raise ContractError.PRICE_OUT_OF_RANGE

        # Check matched amount range overlap
        if match_amount < buy_min_amount or match_amount > buy_max_amount:
            raise ContractError.AMOUNT_OUT_OF_RANGE
        if match_amount < sell_min_amount or match_amount > sell_max_amount:
            raise ContractError.AMOUNT_OUT_OF_RANGE

        # 3. Execution & Settlements
        # Buyer locked quote token collateral. Seller locked base token collateral.
        # Total cost = match_amount * match_price
        # Verify locked collaterals are sufficient
        quote = self.storage.get("quote_token")
        base = self.storage.get("base_token")

        if buy_order["collateral_token"] != quote or sell_order["collateral_token"] != base:
            raise ContractError.COLLATERAL_MISMATCH

        total_cost = match_amount * match_price
        if buy_order["collateral_amount"] < total_cost:
            raise ContractError.AMOUNT_OUT_OF_RANGE
        if sell_order["collateral_amount"] < match_amount:
            raise ContractError.AMOUNT_OUT_OF_RANGE

        contract_addr = self.env.current_contract_address()

        # Send base token (asset) from contract (from seller's collateral) to buyer
        success1 = self.env.invoke_contract(base, "transfer", [contract_addr, buy_order["trader"], match_amount])
        if not success1:
            raise ContractError.TRANSFER_FAILED

        # Send quote token (payment) from contract (from buyer's collateral) to seller
        success2 = self.env.invoke_contract(quote, "transfer", [contract_addr, sell_order["trader"], total_cost])
        if not success2:
            raise ContractError.TRANSFER_FAILED

        # Refund remaining excess collaterals
        excess_buyer_quote = buy_order["collateral_amount"] - total_cost
        if excess_buyer_quote > U128(0):
            self.env.invoke_contract(quote, "transfer", [contract_addr, buy_order["trader"], excess_buyer_quote])

        excess_seller_base = sell_order["collateral_amount"] - match_amount
        if excess_seller_base > U128(0):
            self.env.invoke_contract(base, "transfer", [contract_addr, sell_order["trader"], excess_seller_base])

        # Mark orders as filled
        buy_order["filled"] = True
        buy_order["locked"] = False
        sell_order["filled"] = True
        sell_order["locked"] = False

        self.storage.set(("order", buy_order_id), buy_order)
        self.storage.set(("order", sell_order_id), sell_order)

        self.env.emit_event("trade_executed", {
            "buy_order": buy_order_id,
            "sell_order": sell_order_id,
            "amount": match_amount,
            "price": match_price
        })

        return True

    # ------------------------------------------------------------------ #
    #  View Operations                                                    #
    # ------------------------------------------------------------------ #

    @view
    def get_order_status(self, order_id: U64) -> Map:
        """Get public status of a secret order without exposing parameters."""
        self._require_initialized()
        o = self.storage.get(("order", order_id), None)
        if o is None:
            raise ContractError.ORDER_NOT_FOUND
        
        res = Map()
        res.set(Symbol("trader"), o["trader"])
        res.set(Symbol("collateral_token"), o["collateral_token"])
        res.set(Symbol("locked"), o["locked"])
        res.set(Symbol("filled"), o["filled"])
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                   #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        caller.require_auth()
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_engine(self, caller: Address):
        if not self.storage.get(("engine", caller), False):
            raise ContractError.UNAUTHORIZED
