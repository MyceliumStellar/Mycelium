"""
Commodity Trading — Delivery logs, quality grade parameters, inspection windows, and settlement disbursements.

Mycelium Smart Contract for Stellar. Facilitates buying and selling of physical commodities.
Buyers deposit payments in a stablecoin escrow. Upon shipment, a delivery window starts.
Buyers can accept delivery or raise quality disputes based on independent inspection reports.
Disputes trigger automated discount adjustments based on grade deviation or are resolved via arbitration.
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
    LISTING_NOT_FOUND = 6
    INVALID_STATUS = 7
    EXPIRED = 8
    INSUFFICIENT_ESCROW = 9
    DISPUTE_WINDOW_EXPIRED = 10
    NO_DISPUTE_ACTIVE = 11

@contract
class CommodityTrading:
    """
    Commodity Trading and Escrow Settlement Contract.
    Statuses: LISTED, PURCHASED, SHIPPED, COMPLETED, DISPUTED, CANCELLED
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        arbitrator: Address,
        stablecoin: Address
    ):
        """Initialize configurations and roles."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("arbitrator", arbitrator)
        self.storage.set("stablecoin", stablecoin)
        self.storage.set("listing_nonce", U64(1))
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "arbitrator": arbitrator,
            "stablecoin": stablecoin
        })

    @external
    def create_listing(
        self,
        caller: Address,
        commodity: Symbol,
        quantity: U128,
        price_per_unit: U128,
        target_grade: U64,            # Target quality index/grade (e.g. 100 max)
        delivery_details: Bytes,      # Encoded delivery location/rules
        settlement_window: U64,       # Time to purchase in seconds
        dispute_window: U64           # Time to inspect after shipment in seconds
    ) -> U64:
        """Seller creates a commodity listing with quality grades and terms."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if quantity == U128(0) or price_per_unit == U128(0) or target_grade == U64(0):
            raise ContractError.INVALID_PARAM

        listing_id = self.storage.get("listing_nonce", U64(1))
        self.storage.set("listing_nonce", listing_id + U64(1))

        # Total price = quantity * price_per_unit / scale
        # Assuming units are scaled by 10^7
        total_price = (quantity * price_per_unit) / U128(10_000_000)

        prefix = f"list_{listing_id}_"
        self.storage.set(prefix + "seller", caller)
        self.storage.set(prefix + "buyer", Address(self.env.current_contract_address())) # Temporarily contract
        self.storage.set(prefix + "commodity", commodity)
        self.storage.set(prefix + "quantity", quantity)
        self.storage.set(prefix + "price_per_unit", price_per_unit)
        self.storage.set(prefix + "total_price", total_price)
        self.storage.set(prefix + "target_grade", target_grade)
        self.storage.set(prefix + "delivery_details", delivery_details)
        self.storage.set(prefix + "settlement_window", settlement_window)
        self.storage.set(prefix + "dispute_window", dispute_window)
        self.storage.set(prefix + "creation_time", self._get_now())
        self.storage.set(prefix + "status", Symbol("LISTED"))

        self.env.emit_event("commodity_listed", {
            "listing_id": listing_id,
            "seller": caller,
            "commodity": commodity,
            "total_price": total_price,
            "target_grade": target_grade
        })

        return listing_id

    @external
    def purchase_listing(self, caller: Address, listing_id: U64):
        """Buyer locks stablecoin payment in escrow for the commodity listing."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        prefix = f"list_{listing_id}_"
        status = self.storage.get(prefix + "status")
        if status is None:
            raise ContractError.LISTING_NOT_FOUND
        if status != Symbol("LISTED"):
            raise ContractError.INVALID_STATUS

        creation_time = self.storage.get(prefix + "creation_time", U64(0))
        settlement_window = self.storage.get(prefix + "settlement_window", U64(0))
        if self._get_now() > creation_time + settlement_window:
            raise ContractError.EXPIRED

        total_price = self.storage.get(prefix + "total_price", U128(0))

        # Transfer stablecoin to escrow
        stablecoin = self.storage.get("stablecoin")
        contract_addr = self.env.current_contract_address()
        self.env.call(stablecoin, "transfer", caller, contract_addr, total_price)

        self.storage.set(prefix + "buyer", caller)
        self.storage.set(prefix + "status", Symbol("PURCHASED"))
        self.storage.set(prefix + "purchase_time", self._get_now())

        self.env.emit_event("commodity_purchased", {
            "listing_id": listing_id,
            "buyer": caller,
            "escrowed_amount": total_price
        })

    @external
    def ship_commodity(self, caller: Address, listing_id: U64, shipment_hash: Bytes):
        """Seller marks commodity as shipped and logs shipment document hash."""
        caller.require_auth()
        self._require_initialized()

        prefix = f"list_{listing_id}_"
        status = self.storage.get(prefix + "status")
        if status != Symbol("PURCHASED"):
            raise ContractError.INVALID_STATUS

        seller = self.storage.get(prefix + "seller")
        if caller != seller:
            raise ContractError.UNAUTHORIZED

        self.storage.set(prefix + "status", Symbol("SHIPPED"))
        self.storage.set(prefix + "shipment_time", self._get_now())
        self.storage.set(prefix + "shipment_hash", shipment_hash)

        self.env.emit_event("commodity_shipped", {
            "listing_id": listing_id,
            "shipment_hash": shipment_hash
        })

    @external
    def accept_delivery(self, caller: Address, listing_id: U64):
        """Buyer accepts delivery, releasing escrow payment to seller."""
        caller.require_auth()
        self._require_initialized()

        prefix = f"list_{listing_id}_"
        status = self.storage.get(prefix + "status")
        if status != Symbol("SHIPPED"):
            raise ContractError.INVALID_STATUS

        buyer = self.storage.get(prefix + "buyer")
        if caller != buyer:
            raise ContractError.UNAUTHORIZED

        total_price = self.storage.get(prefix + "total_price", U128(0))
        seller = self.storage.get(prefix + "seller")

        self.storage.set(prefix + "status", Symbol("COMPLETED"))

        # Transfer full payment from escrow to seller
        stablecoin = self.storage.get("stablecoin")
        self.env.call(stablecoin, "transfer", self.env.current_contract_address(), seller, total_price)

        self.env.emit_event("delivery_accepted", {
            "listing_id": listing_id,
            "payout": total_price
        })

    @external
    def raise_quality_dispute(
        self,
        caller: Address,
        listing_id: U64,
        inspection_grade: U64,
        inspection_report_hash: Bytes
    ):
        """
        Buyer raises dispute based on quality mismatch in independent inspection.
        Starts dispute process if target quality grade was not met.
        """
        caller.require_auth()
        self._require_initialized()

        prefix = f"list_{listing_id}_"
        status = self.storage.get(prefix + "status")
        if status != Symbol("SHIPPED"):
            raise ContractError.INVALID_STATUS

        buyer = self.storage.get(prefix + "buyer")
        if caller != buyer:
            raise ContractError.UNAUTHORIZED

        shipment_time = self.storage.get(prefix + "shipment_time", U64(0))
        dispute_window = self.storage.get(prefix + "dispute_window", U64(0))
        if self._get_now() > shipment_time + dispute_window:
            raise ContractError.DISPUTE_WINDOW_EXPIRED

        target_grade = self.storage.get(prefix + "target_grade", U64(0))
        if inspection_grade >= target_grade:
            # Inspection passed target grade, cannot dispute quality mismatch
            raise ContractError.INVALID_PARAM

        self.storage.set(prefix + "status", Symbol("DISPUTED"))
        self.storage.set(prefix + "inspection_grade", inspection_grade)
        self.storage.set(prefix + "inspection_hash", inspection_report_hash)

        self.env.emit_event("quality_disputed", {
            "listing_id": listing_id,
            "target_grade": target_grade,
            "actual_grade": inspection_grade,
            "report_hash": inspection_report_hash
        })

    @external
    def resolve_dispute(
        self,
        caller: Address,
        listing_id: U64,
        approved_discount_bps: U64 # Percentage refund to buyer, e.g. 1000 for 10% refund
    ):
        """
        Arbitrator resolves quality dispute, splitting the escrowed funds.
        """
        caller.require_auth()
        self._require_initialized()

        arbitrator = self.storage.get("arbitrator")
        if caller != arbitrator:
            raise ContractError.UNAUTHORIZED

        prefix = f"list_{listing_id}_"
        status = self.storage.get(prefix + "status")
        if status != Symbol("DISPUTED"):
            raise ContractError.NO_DISPUTE_ACTIVE

        if approved_discount_bps > U64(10000):
            raise ContractError.INVALID_PARAM

        total_price = self.storage.get(prefix + "total_price", U128(0))
        buyer = self.storage.get(prefix + "buyer")
        seller = self.storage.get(prefix + "seller")

        # Split math
        buyer_refund = (total_price * U128(approved_discount_bps)) / U128(10000)
        seller_payout = total_price - buyer_refund

        self.storage.set(prefix + "status", Symbol("COMPLETED"))

        stablecoin = self.storage.get("stablecoin")
        contract_addr = self.env.current_contract_address()

        if buyer_refund > U128(0):
            self.env.call(stablecoin, "transfer", contract_addr, buyer, buyer_refund)
        if seller_payout > U128(0):
            self.env.call(stablecoin, "transfer", contract_addr, seller, seller_payout)

        self.env.emit_event("dispute_resolved", {
            "listing_id": listing_id,
            "buyer_refund": buyer_refund,
            "seller_payout": seller_payout
        })

    @external
    def cancel_listing(self, caller: Address, listing_id: U64):
        """Seller cancels listing before anyone purchases it."""
        caller.require_auth()
        self._require_initialized()

        prefix = f"list_{listing_id}_"
        status = self.storage.get(prefix + "status")
        if status != Symbol("LISTED"):
            raise ContractError.INVALID_STATUS

        seller = self.storage.get(prefix + "seller")
        if caller != seller:
            raise ContractError.UNAUTHORIZED

        self.storage.set(prefix + "status", Symbol("CANCELLED"))

        self.env.emit_event("listing_cancelled", {
            "listing_id": listing_id
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause listing creations (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_listing_details(self, listing_id: U64) -> Map:
        """Query listing data."""
        res = Map(self.env)
        prefix = f"list_{listing_id}_"
        seller = self.storage.get(prefix + "seller")
        if seller is not None:
            res.set("seller", seller)
            res.set("buyer", self.storage.get(prefix + "buyer"))
            res.set("commodity", self.storage.get(prefix + "commodity"))
            res.set("quantity", self.storage.get(prefix + "quantity"))
            res.set("price_per_unit", self.storage.get(prefix + "price_per_unit"))
            res.set("total_price", self.storage.get(prefix + "total_price"))
            res.set("target_grade", self.storage.get(prefix + "target_grade"))
            res.set("status", self.storage.get(prefix + "status"))
            res.set("shipment_hash", self.storage.get(prefix + "shipment_hash"))
            res.set("inspection_grade", self.storage.get(prefix + "inspection_grade"))
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
