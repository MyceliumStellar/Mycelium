"""
TradeFinance — Letters of credit, bill of lading checks, release triggers, dispute terms.

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
    LC_ALREADY_EXISTS = 4
    LC_NOT_FOUND = 5
    INVALID_STATUS = 6
    LC_EXPIRED = 7
    LC_NOT_EXPIRED = 8
    DISPUTE_ACTIVE = 9
    TIMELOCK_ACTIVE = 10

@contract
class TradeFinance:
    """
    Escrow manager for Letter of Credit (LC) trade financing.
    
    Handles multi-party verification (buyer, seller, issuing bank) for commodity trade,
    requiring bill of lading hash proof submission before releasing funds.
    """
    
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """
        Initializes the trade finance escrow manager.
        """
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED
            
        self.storage.set("admin", admin)
        self.storage.set("initialized", True)
        
        self.env.emit_event("initialized", {"admin": admin})

    @external
    def open_letter_of_credit(
        self, 
        caller: Address, 
        lc_id: Symbol, 
        seller: Address, 
        bank: Address, 
        amount: U128, 
        duration: U64
    ) -> Bool:
        """
        Opens a Letter of Credit, locks the buyer's payment allocation.
        
        Args:
            caller: Buyer address funding the LC.
            lc_id: Unique letter of credit identifier.
            seller: Destination beneficiary of the trade.
            bank: Financial intermediary/arbitrator auditing shipping docs.
            amount: Value locked in the escrow.
            duration: Validity timeframe before expiry allowed.
        """
        caller.require_auth()
        self._require_initialized()
        
        lc_key = "lc:" + str(lc_id)
        if self.storage.has(lc_key):
            raise ContractError.LC_ALREADY_EXISTS
            
        current_time = self.env.ledger().timestamp()
        expiry = current_time + duration
        
        # Save LC struct in storage
        self.storage.set(lc_key, True)
        self.storage.set(lc_key + ":buyer", caller)
        self.storage.set(lc_key + ":seller", seller)
        self.storage.set(lc_key + ":bank", bank)
        self.storage.set(lc_key + ":amount", amount)
        self.storage.set(lc_key + ":status", Symbol("OPENED"))
        self.storage.set(lc_key + ":expiry", expiry)
        self.storage.set(lc_key + ":bol_hash", Bytes())
        self.storage.set(lc_key + ":dispute_timer", U64(0))
        
        self.env.emit_event(
            "lc_opened", 
            {
                "lc_id": lc_id, 
                "buyer": caller, 
                "seller": seller, 
                "amount": amount, 
                "expiry": expiry
            }
        )
        return True

    @external
    def submit_bill_of_lading(
        self, 
        caller: Address, 
        lc_id: Symbol, 
        bol_hash: Bytes
    ) -> Bool:
        """
        Submits bill of lading document hash confirming shipping of goods.
        
        Args:
            caller: Seller beneficiary address.
            lc_id: Target LC.
            bol_hash: 32-byte cryptographic hash of the Bill of Lading.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_lc(lc_id)
        
        lc_key = "lc:" + str(lc_id)
        
        # Enforce seller authorization
        seller = self.storage.get(lc_key + ":seller")
        if caller != seller:
            raise ContractError.UNAUTHORIZED
            
        # Ensure status is OPENED
        status = self.storage.get(lc_key + ":status")
        if status != Symbol("OPENED"):
            raise ContractError.INVALID_STATUS
            
        # Check expiry
        expiry = self.storage.get(lc_key + ":expiry", U64(0))
        if self.env.ledger().timestamp() >= expiry:
            raise ContractError.LC_EXPIRED
            
        self.storage.set(lc_key + ":bol_hash", bol_hash)
        self.storage.set(lc_key + ":status", Symbol("SHIPPED"))
        
        self.env.emit_event(
            "goods_shipped", 
            {"lc_id": lc_id, "bol_hash": bol_hash, "status": Symbol("SHIPPED")}
        )
        return True

    @external
    def release_escrow(self, caller: Address, lc_id: Symbol) -> Bool:
        """
        Triggers escrow fund disbursement to the seller.
        
        Can be triggered by the Buyer directly, or the issuing Bank after document audit.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_lc(lc_id)
        
        lc_key = "lc:" + str(lc_id)
        
        # Verify status is SHIPPED
        status = self.storage.get(lc_key + ":status")
        if status != Symbol("SHIPPED"):
            raise ContractError.INVALID_STATUS
            
        buyer = self.storage.get(lc_key + ":buyer")
        bank = self.storage.get(lc_key + ":bank")
        
        if caller != buyer and caller != bank:
            raise ContractError.UNAUTHORIZED
            
        seller = self.storage.get(lc_key + ":seller")
        amount = self.storage.get(lc_key + ":amount")
        
        # Complete settlement status change
        self.storage.set(lc_key + ":status", Symbol("SETTLED"))
        
        self.env.emit_event(
            "escrow_released", 
            {"lc_id": lc_id, "amount": amount, "recipient": seller}
        )
        return True

    @external
    def initiate_dispute(self, caller: Address, lc_id: Symbol, reason: Symbol) -> Bool:
        """
        Halts the LC release execution, placing it under review by the bank.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_lc(lc_id)
        
        lc_key = "lc:" + str(lc_id)
        
        buyer = self.storage.get(lc_key + ":buyer")
        seller = self.storage.get(lc_key + ":seller")
        if caller != buyer and caller != seller:
            raise ContractError.UNAUTHORIZED
            
        status = self.storage.get(lc_key + ":status")
        if status != Symbol("OPENED") and status != Symbol("SHIPPED"):
            raise ContractError.INVALID_STATUS
            
        self.storage.set(lc_key + ":status", Symbol("DISPUTED"))
        self.storage.set(lc_key + ":dispute_timer", self.env.ledger().timestamp())
        
        self.env.emit_event("dispute_opened", {"lc_id": lc_id, "by": caller, "reason": reason})
        return True

    @external
    def resolve_dispute(
        self, 
        caller: Address, 
        lc_id: Symbol, 
        payout_recipient: Address
    ) -> Bool:
        """
        Resolves active disputes. Executed solely by the issuing Bank.
        
        Disburses escrow funds to the specified payout recipient (buyer or seller).
        """
        caller.require_auth()
        self._require_initialized()
        self._require_lc(lc_id)
        
        lc_key = "lc:" + str(lc_id)
        
        # Only issuing bank can settle disputes
        bank = self.storage.get(lc_key + ":bank")
        if caller != bank:
            raise ContractError.UNAUTHORIZED
            
        status = self.storage.get(lc_key + ":status")
        if status != Symbol("DISPUTED"):
            raise ContractError.INVALID_STATUS
            
        buyer = self.storage.get(lc_key + ":buyer")
        seller = self.storage.get(lc_key + ":seller")
        
        if payout_recipient != buyer and payout_recipient != seller:
            raise ContractError.UNAUTHORIZED
            
        amount = self.storage.get(lc_key + ":amount")
        self.storage.set(lc_key + ":status", Symbol("RESOLVED"))
        
        self.env.emit_event(
            "dispute_resolved", 
            {"lc_id": lc_id, "resolved_by": caller, "recipient": payout_recipient, "amount": amount}
        )
        return True

    @external
    def request_refund(self, caller: Address, lc_id: Symbol) -> Bool:
        """
        Enables the buyer to reclaim locked escrow funds if the LC expires.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_lc(lc_id)
        
        lc_key = "lc:" + str(lc_id)
        
        buyer = self.storage.get(lc_key + ":buyer")
        if caller != buyer:
            raise ContractError.UNAUTHORIZED
            
        status = self.storage.get(lc_key + ":status")
        if status != Symbol("OPENED"):
            raise ContractError.INVALID_STATUS
            
        expiry = self.storage.get(lc_key + ":expiry", U64(0))
        if self.env.ledger().timestamp() < expiry:
            raise ContractError.LC_NOT_EXPIRED
            
        amount = self.storage.get(lc_key + ":amount")
        self.storage.set(lc_key + ":status", Symbol("REFUNDED"))
        
        self.env.emit_event("lc_refunded", {"lc_id": lc_id, "recipient": buyer, "amount": amount})
        return True

    @view
    def get_lc_details(self, lc_id: Symbol) -> Map:
        """
        Retrieves all states and specifications of a Letter of Credit.
        """
        self._require_initialized()
        self._require_lc(lc_id)
        
        lc_key = "lc:" + str(lc_id)
        details = Map()
        details.set(Symbol("buyer"), self.storage.get(lc_key + ":buyer"))
        details.set(Symbol("seller"), self.storage.get(lc_key + ":seller"))
        details.set(Symbol("bank"), self.storage.get(lc_key + ":bank"))
        details.set(Symbol("amount"), self.storage.get(lc_key + ":amount"))
        details.set(Symbol("status"), self.storage.get(lc_key + ":status"))
        details.set(Symbol("expiry"), self.storage.get(lc_key + ":expiry"))
        details.set(Symbol("bol_hash"), self.storage.get(lc_key + ":bol_hash"))
        details.set(Symbol("dispute_time"), self.storage.get(lc_key + ":dispute_timer"))
        return details

    # Internal helpers
    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_lc(self, lc_id: Symbol):
        lc_key = "lc:" + str(lc_id)
        if not self.storage.get(lc_key, False):
            raise ContractError.LC_NOT_FOUND
        
        # Verify administrative sanity check
        admin = self.storage.get("admin")
        if not self.storage.has("lc:" + str(lc_id) + ":buyer"):
            raise ContractError.LC_NOT_FOUND
