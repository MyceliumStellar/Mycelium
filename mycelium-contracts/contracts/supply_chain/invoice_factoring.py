"""
Invoice Factoring — Receivables factoring, discount bids, credit limits, and reserve repayment settlements.

Mycelium Smart Contract for Stellar. Enables suppliers (sellers) to sell unpaid debtor invoices to factors (investors) at a discount.
Tracks credit limits, discount rates, and advance rates. Disburses advance payments, collects final debtor payments,
and distributes the remaining reserve minus factoring fees to the seller.
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
    INVALID_LIMIT = 5
    INVOICE_NOT_FOUND = 6
    INVALID_STATUS = 7
    LIMIT_EXCEEDED = 8
    BID_NOT_FOUND = 9
    EXPIRED = 10
    INSUFFICIENT_FUNDS = 11

@contract
class InvoiceFactoring:
    """
    Invoice Factoring contract facilitating receivables financing.
    - Statuses: PENDING, FACTORED, REPAID, DEFAULTED
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, stablecoin: Address):
        """Initialize configurations and admin controls."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("stablecoin", stablecoin)
        self.storage.set("invoice_nonce", U64(1))
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "stablecoin": stablecoin
        })

    @external
    def set_factoring_limit(self, caller: Address, user: Address, limit: U128):
        """Set credit limits for users (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        self.storage.set(f"limit_{user}", limit)
        self.env.emit_event("limit_updated", {
            "user": user,
            "new_limit": limit
        })

    @external
    def register_invoice(
        self,
        caller: Address,
        debtor: Address,
        amount: U128,
        due_date: U64,
        doc_hash: Bytes
    ) -> U64:
        """
        Seller registers a new invoice to be factored.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if amount == U128(0) or due_date <= self._get_now():
            raise ContractError.INVALID_LIMIT

        # Check that this invoice does not exceed seller limit
        limit = self.storage.get(f"limit_{caller}", U128(0))
        outstanding = self.storage.get(f"outstanding_{caller}", U128(0))
        if outstanding + amount > limit:
            raise ContractError.LIMIT_EXCEEDED

        invoice_id = self.storage.get("invoice_nonce", U64(1))
        self.storage.set("invoice_nonce", invoice_id + U64(1))

        prefix = f"inv_{invoice_id}_"
        self.storage.set(prefix + "seller", caller)
        self.storage.set(prefix + "debtor", debtor)
        self.storage.set(prefix + "amount", amount)
        self.storage.set(prefix + "due_date", due_date)
        self.storage.set(prefix + "doc_hash", doc_hash)
        self.storage.set(prefix + "status", Symbol("PENDING"))
        self.storage.set(prefix + "bid_count", U64(0))

        self.env.emit_event("invoice_registered", {
            "invoice_id": invoice_id,
            "seller": caller,
            "debtor": debtor,
            "amount": amount,
            "due_date": due_date
        })

        return invoice_id

    @external
    def submit_bid(
        self,
        caller: Address,
        invoice_id: U64,
        advance_rate_bps: U64,  # e.g. 8000 for 80% advance payment
        discount_fee_bps: U64   # e.g. 300 for 3% factoring discount fee
    ) -> U64:
        """
        Factor submits a discount bid on a pending invoice.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        prefix = f"inv_{invoice_id}_"
        status = self.storage.get(prefix + "status")
        if status is None:
            raise ContractError.INVOICE_NOT_FOUND
        if status != Symbol("PENDING"):
            raise ContractError.INVALID_STATUS

        due_date = self.storage.get(prefix + "due_date", U64(0))
        if due_date <= self._get_now():
            raise ContractError.EXPIRED

        if advance_rate_bps > U64(10000) or discount_fee_bps > advance_rate_bps:
            raise ContractError.INVALID_LIMIT

        bid_count = self.storage.get(prefix + "bid_count", U64(0))
        bid_id = bid_count + U64(1)
        self.storage.set(prefix + "bid_count", bid_id)

        bid_prefix = f"bid_{invoice_id}_{bid_id}_"
        self.storage.set(bid_prefix + "factor", caller)
        self.storage.set(bid_prefix + "advance_rate", advance_rate_bps)
        self.storage.set(bid_prefix + "discount_fee", discount_fee_bps)
        self.storage.set(bid_prefix + "status", Symbol("ACTIVE"))

        self.env.emit_event("bid_submitted", {
            "invoice_id": invoice_id,
            "bid_id": bid_id,
            "factor": caller,
            "advance_rate": advance_rate_bps,
            "discount_fee": discount_fee_bps
        })

        return bid_id

    @external
    def accept_bid(self, caller: Address, invoice_id: U64, bid_id: U64):
        """
        Seller accepts a bid, triggering early payout of the advance amount.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        prefix = f"inv_{invoice_id}_"
        seller = self.storage.get(prefix + "seller")
        if seller is None:
            raise ContractError.INVOICE_NOT_FOUND

        if caller != seller:
            raise ContractError.UNAUTHORIZED

        status = self.storage.get(prefix + "status")
        if status != Symbol("PENDING"):
            raise ContractError.INVALID_STATUS

        bid_prefix = f"bid_{invoice_id}_{bid_id}_"
        bid_status = self.storage.get(bid_prefix + "status")
        if bid_status != Symbol("ACTIVE"):
            raise ContractError.BID_NOT_FOUND

        # Fetch bid rates
        factor = self.storage.get(bid_prefix + "factor")
        advance_rate = self.storage.get(bid_prefix + "advance_rate", U64(0))
        discount_fee = self.storage.get(bid_prefix + "discount_fee", U64(0))
        amount = self.storage.get(prefix + "amount", U128(0))

        # Calculate advance and reserve
        advance_amount = (amount * U128(advance_rate)) / U128(10000)
        
        # Factor transfers advance_amount to the seller
        stablecoin = self.storage.get("stablecoin")
        contract_addr = self.env.current_contract_address()
        self.env.call(stablecoin, "transfer", factor, seller, advance_amount)

        # Update status
        self.storage.set(prefix + "status", Symbol("FACTORED"))
        self.storage.set(prefix + "selected_bid", bid_id)
        
        # Track seller outstanding
        outstanding = self.storage.get(f"outstanding_{seller}", U128(0))
        self.storage.set(f"outstanding_{seller}", outstanding + amount)

        self.env.emit_event("invoice_factored", {
            "invoice_id": invoice_id,
            "bid_id": bid_id,
            "factor": factor,
            "advance_payout": advance_amount
        })

    @external
    def repay_invoice(self, caller: Address, invoice_id: U64):
        """
        Debtor repays the full invoice amount to the contract.
        Contract settles advance repayment to the factor and distributes remaining reserve minus fees to the seller.
        """
        caller.require_auth()
        self._require_initialized()

        prefix = f"inv_{invoice_id}_"
        status = self.storage.get(prefix + "status")
        if status != Symbol("FACTORED"):
            raise ContractError.INVALID_STATUS

        debtor = self.storage.get(prefix + "debtor")
        if caller != debtor:
            raise ContractError.UNAUTHORIZED

        amount = self.storage.get(prefix + "amount", U128(0))
        seller = self.storage.get(prefix + "seller")
        bid_id = self.storage.get(prefix + "selected_bid", U64(0))

        bid_prefix = f"bid_{invoice_id}_{bid_id}_"
        factor = self.storage.get(bid_prefix + "factor")
        advance_rate = self.storage.get(bid_prefix + "advance_rate", U64(0))
        discount_fee_bps = self.storage.get(bid_prefix + "discount_fee", U64(0))

        # Debtor transfers full amount to contract
        stablecoin = self.storage.get("stablecoin")
        contract_addr = self.env.current_contract_address()
        self.env.call(stablecoin, "transfer", debtor, contract_addr, amount)

        # Calculations
        advance_amount = (amount * U128(advance_rate)) / U128(10000)
        fee_amount = (amount * U128(discount_fee_bps)) / U128(10000)
        reserve_amount = amount - advance_amount

        # Factor gets refunded advance + discount fee
        factor_payout = advance_amount + fee_amount
        
        # Seller gets reserve - discount fee
        seller_payout = reserve_amount - fee_amount

        # Distribute payouts
        if factor_payout > U128(0):
            self.env.call(stablecoin, "transfer", contract_addr, factor, factor_payout)
        if seller_payout > U128(0):
            self.env.call(stablecoin, "transfer", contract_addr, seller, seller_payout)

        # Update invoice state
        self.storage.set(prefix + "status", Symbol("REPAID"))
        
        # Reduce outstanding limit of seller
        outstanding = self.storage.get(f"outstanding_{seller}", U128(0))
        if outstanding >= amount:
            self.storage.set(f"outstanding_{seller}", outstanding - amount)
        else:
            self.storage.set(f"outstanding_{seller}", U128(0))

        self.env.emit_event("invoice_repaid", {
            "invoice_id": invoice_id,
            "debtor": debtor,
            "seller_received": seller_payout,
            "factor_received": factor_payout
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause factoring operations (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_invoice_details(self, invoice_id: U64) -> Map:
        """Query invoice info."""
        res = Map(self.env)
        prefix = f"inv_{invoice_id}_"
        seller = self.storage.get(prefix + "seller")
        if seller is not None:
            res.set("seller", seller)
            res.set("debtor", self.storage.get(prefix + "debtor"))
            res.set("amount", self.storage.get(prefix + "amount"))
            res.set("due_date", self.storage.get(prefix + "due_date"))
            res.set("status", self.storage.get(prefix + "status"))
            res.set("selected_bid", self.storage.get(prefix + "selected_bid"))
        return res

    @view
    def get_bid_details(self, invoice_id: U64, bid_id: U64) -> Map:
        """Query bid parameters."""
        res = Map(self.env)
        bid_prefix = f"bid_{invoice_id}_{bid_id}_"
        factor = self.storage.get(bid_prefix + "factor")
        if factor is not None:
            res.set("factor", factor)
            res.set("advance_rate", self.storage.get(bid_prefix + "advance_rate"))
            res.set("discount_fee", self.storage.get(bid_prefix + "discount_fee"))
            res.set("status", self.storage.get(bid_prefix + "status"))
        return res

    @view
    def get_user_outstanding(self, user: Address) -> Map:
        """Query credit limit and outstanding factored debt."""
        res = Map(self.env)
        res.set("limit", self.storage.get(f"limit_{user}", U128(0)))
        res.set("outstanding", self.storage.get(f"outstanding_{user}", U128(0)))
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
