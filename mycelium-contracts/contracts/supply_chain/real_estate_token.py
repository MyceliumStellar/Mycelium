"""
RealEstateToken — Property split shares, yield distributions, tax reserves, management controls.

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
    INSUFFICIENT_SHARES = 4
    INVALID_RATE = 5
    INSUFFICIENT_RESERVES = 6
    INVALID_AMOUNT = 7
    TRANSFER_RESTRICTED = 8

@contract
class RealEstateToken:
    """
    Fractionalized real estate tokenization and yield manager.
    
    Splits property value into equity shares, manages rent/yield payouts, 
    withholds property tax reserves, and locks share transfers until pending yields are claimed.
    """
    
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self, 
        admin: Address, 
        property_value: U128, 
        total_shares: U128, 
        tax_reserve_rate: U64
    ):
        """
        Initializes the property fractionalization parameters.
        
        Args:
            admin: Address of the property manager admin.
            property_value: Stated appraisal value of the property in base currency.
            total_shares: Total pool of shares representing fractional ownership.
            tax_reserve_rate: Rate in basis points (e.g. 1000 = 10%) reserved for taxes.
        """
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED
            
        if tax_reserve_rate > U64(10000): # Cannot exceed 100%
            raise ContractError.INVALID_RATE
            
        if total_shares == U128(0):
            raise ContractError.INVALID_AMOUNT
            
        self.storage.set("admin", admin)
        self.storage.set("prop_value", property_value)
        self.storage.set("total_shares", total_shares)
        self.storage.set("tax_rate", tax_reserve_rate)
        self.storage.set("tax_held", U128(0))
        self.storage.set("yield_index", U64(0))
        
        # Allocate initial shares to admin / issuer
        self.storage.set("bal:" + str(admin), total_shares)
        self.storage.set("last_y_idx:" + str(admin), U64(0))
        
        self.storage.set("initialized", True)
        
        self.env.emit_event(
            "initialized", 
            {"admin": admin, "value": property_value, "shares": total_shares}
        )

    @external
    def transfer_shares(self, caller: Address, recipient: Address, amount: U128) -> Bool:
        """
        Transfers fractional property shares to another address.
        
        CRITICAL EDGE CASE: Requires both the sender and recipient to claim any 
        unclaimed historical yields first to prevent "dividend washing/stealing".
        """
        caller.require_auth()
        self._require_initialized()
        
        if amount == U128(0):
            raise ContractError.INVALID_AMOUNT
            
        bal_key_src = "bal:" + str(caller)
        src_bal = self.storage.get(bal_key_src, U128(0))
        if src_bal < amount:
            raise ContractError.INSUFFICIENT_SHARES
            
        # Force Yield Settlement for Sender
        pending_src = self._get_pending_yield_amount(caller)
        if pending_src > U128(0):
            raise ContractError.TRANSFER_RESTRICTED
            
        # Force Yield Settlement for Recipient
        pending_rec = self._get_pending_yield_amount(recipient)
        if pending_rec > U128(0):
            raise ContractError.TRANSFER_RESTRICTED
            
        # Complete fractional transfer
        bal_key_rec = "bal:" + str(recipient)
        rec_bal = self.storage.get(bal_key_rec, U128(0))
        
        self.storage.set(bal_key_src, src_bal - amount)
        self.storage.set(bal_key_rec, rec_bal + amount)
        
        self.env.emit_event("shares_transferred", {"from": caller, "to": recipient, "amount": amount})
        return True

    @external
    def distribute_yield(self, caller: Address, gross_yield: U128) -> Bool:
        """
        Distributes rental income or commercial yield to fractional owners.
        
        Deducts property tax reserves before setting up the distribution pool.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        if gross_yield == U128(0):
            raise ContractError.INVALID_AMOUNT
            
        tax_rate = self.storage.get("tax_rate", U64(0))
        
        # Calculate reserve withholding: gross_yield * tax_rate / 10000
        tax_withholding = (gross_yield * U128(tax_rate)) / U128(10000)
        net_yield = gross_yield - tax_withholding
        
        total_shares = self.storage.get("total_shares", U128(1))
        # Amount per share with precision multiplier (10^12) to avoid truncation
        pps = (net_yield * U128(1000000000000)) / total_shares
        
        # Record distribution event
        index = self.storage.get("yield_index", U64(0))
        dist_key = "dist:" + str(index)
        self.storage.set(dist_key + ":pps", pps)
        self.storage.set(dist_key + ":net", net_yield)
        self.storage.set(dist_key + ":time", self.env.ledger().timestamp())
        
        # Accumulate reserve balances
        tax_held = self.storage.get("tax_held", U128(0))
        self.storage.set("tax_held", tax_held + tax_withholding)
        self.storage.set("yield_index", index + U64(1))
        
        self.env.emit_event(
            "yield_distributed", 
            {"index": index, "gross": gross_yield, "net": net_yield, "tax": tax_withholding}
        )
        return True

    @external
    def claim_yield(self, caller: Address) -> U128:
        """
        Calculates and claims outstanding yield distributions for the caller.
        """
        caller.require_auth()
        self._require_initialized()
        
        pending_payout = self._get_pending_yield_amount(caller)
        if pending_payout == U128(0):
            return U128(0)
            
        # Update claimant's yield index checkpoint
        current_global_index = self.storage.get("yield_index", U64(0))
        self.storage.set("last_y_idx:" + str(caller), current_global_index)
        
        # Simulate payout disbursement
        # In a multi-token deployment, this calls ERC20/SAC token transfer logic.
        self.env.emit_event("yield_claimed", {"recipient": caller, "amount": pending_payout})
        return pending_payout

    @external
    def withdraw_tax_reserves(self, caller: Address, amount: U128, recipient: Address) -> Bool:
        """
        Allows property manager to draw from the tax reserve to pay property taxes.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        reserves = self.storage.get("tax_held", U128(0))
        if amount > reserves:
            raise ContractError.INSUFFICIENT_RESERVES
            
        self.storage.set("tax_held", reserves - amount)
        
        self.env.emit_event("tax_reserves_withdrawn", {"recipient": recipient, "amount": amount})
        return True

    @view
    def get_share_balance(self, user: Address) -> U128:
        """
        Returns fractional shares owned by an investor.
        """
        self._require_initialized()
        return self.storage.get("bal:" + str(user), U128(0))

    @view
    def get_pending_yield(self, user: Address) -> U128:
        """
        Returns accumulated unclaimed yield for a user.
        """
        self._require_initialized()
        return self._get_pending_yield_amount(user)

    @view
    def get_financial_reports(self) -> Map:
        """
        Returns financial summaries of the property.
        """
        self._require_initialized()
        report = Map()
        report.set(Symbol("appraised_value"), self.storage.get("prop_value"))
        report.set(Symbol("total_shares"), self.storage.get("total_shares"))
        report.set(Symbol("tax_reserves_held"), self.storage.get("tax_held"))
        report.set(Symbol("total_distributions_count"), self.storage.get("yield_index"))
        return report

    # Internal helpers
    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _get_pending_yield_amount(self, user: Address) -> U128:
        """
        Aggregates yield payouts from the user's last checkpoint index to the current index.
        """
        user_bal = self.storage.get("bal:" + str(user), U128(0))
        if user_bal == U128(0):
            return U128(0)
            
        last_idx = self.storage.get("last_y_idx:" + str(user), U64(0))
        global_idx = self.storage.get("yield_index", U64(0))
        
        if last_idx >= global_idx:
            return U128(0)
            
        accumulated_pps = U128(0)
        idx = last_idx
        while idx < global_idx:
            pps = self.storage.get("dist:" + str(idx) + ":pps", U128(0))
            accumulated_pps += pps
            idx += U64(1)
            
        # Payout: user_shares * accumulated_pps / precision_multiplier (10^12)
        payout = (user_bal * accumulated_pps) / U128(1000000000000)
        return payout
