"""
CarbonCredit — Retirement registries, emissions metrics validation, carbon offset tracking.

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
    PROJECT_ALREADY_EXISTS = 4
    PROJECT_NOT_FOUND = 5
    INSUFFICIENT_BALANCE = 6
    INVALID_AMOUNT = 7
    RECORD_NOT_FOUND = 8

@contract
class CarbonCredit:
    """
    Registry for carbon offset credits, emissions tracking, and offsets retirement.
    
    Verifies environmental developers' emission reports and records formal
    retirement offsets with justification hashes.
    """
    
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """
        Initializes the carbon credit registry.
        """
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED
            
        self.storage.set("admin", admin)
        self.storage.set("retirement_count", U64(0))
        self.storage.set("initialized", True)
        
        self.env.emit_event("initialized", {"admin": admin})

    @external
    def register_project(
        self, 
        caller: Address, 
        project_id: Symbol, 
        developer: Address, 
        credit_type: Symbol, 
        tonnage: U128, 
        metrics_hash: Bytes
    ) -> Bool:
        """
        Registers a verified carbon avoidance/removal project.
        
        Args:
            caller: Admin/Verifier address.
            project_id: Unique symbol of the carbon project.
            developer: The entity managing the project.
            credit_type: Type of offset (e.g. Symbol("Reforestation")).
            tonnage: Certified metric tons of CO2 equivalent offset.
            metrics_hash: Hash of the verified scientific measurements report.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        proj_key = "proj:" + str(project_id)
        if self.storage.has(proj_key):
            raise ContractError.PROJECT_ALREADY_EXISTS
            
        self.storage.set(proj_key, True)
        self.storage.set(proj_key + ":developer", developer)
        self.storage.set(proj_key + ":type", credit_type)
        self.storage.set(proj_key + ":tonnage", tonnage)
        self.storage.set(proj_key + ":metrics_hash", metrics_hash)
        self.storage.set(proj_key + ":minted", U128(0))
        
        self.env.emit_event(
            "project_registered", 
            {"project_id": project_id, "developer": developer, "tonnage": tonnage}
        )
        return True

    @external
    def mint_credits(
        self, 
        caller: Address, 
        project_id: Symbol, 
        recipient: Address, 
        amount: U128
    ) -> Bool:
        """
        Mints carbon credits corresponding to certified avoidance tonnage.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        proj_key = "proj:" + str(project_id)
        if not self.storage.has(proj_key):
            raise ContractError.PROJECT_NOT_FOUND
            
        if amount == U128(0):
            raise ContractError.INVALID_AMOUNT
            
        tonnage = self.storage.get(proj_key + ":tonnage")
        minted = self.storage.get(proj_key + ":minted", U128(0))
        
        # Prevent minting more credits than certified tonnage
        if minted + amount > tonnage:
            raise ContractError.INVALID_AMOUNT
            
        self.storage.set(proj_key + ":minted", minted + amount)
        
        # Credit recipient balance
        rec_bal_key = "bal:" + str(recipient)
        rec_bal = self.storage.get(rec_bal_key, U128(0))
        self.storage.set(rec_bal_key, rec_bal + amount)
        
        self.env.emit_event(
            "credits_minted", 
            {"project_id": project_id, "recipient": recipient, "amount": amount}
        )
        return True

    @external
    def transfer_credits(self, caller: Address, recipient: Address, amount: U128) -> Bool:
        """
        Transfers carbon credits to another trading account.
        """
        caller.require_auth()
        self._require_initialized()
        
        if amount == U128(0):
            raise ContractError.INVALID_AMOUNT
            
        src_bal_key = "bal:" + str(caller)
        src_bal = self.storage.get(src_bal_key, U128(0))
        if src_bal < amount:
            raise ContractError.INSUFFICIENT_BALANCE
            
        rec_bal_key = "bal:" + str(recipient)
        rec_bal = self.storage.get(rec_bal_key, U128(0))
        
        self.storage.set(src_bal_key, src_bal - amount)
        self.storage.set(rec_bal_key, rec_bal + amount)
        
        self.env.emit_event("credits_transferred", {"from": caller, "to": recipient, "amount": amount})
        return True

    @external
    def retire_credits(
        self, 
        caller: Address, 
        project_id: Symbol, 
        amount: U128, 
        justification_hash: Bytes
    ) -> Bool:
        """
        Retires carbon credits to offset emissions, locking them permanently.
        
        Deducts balance, adds to retirement registry, and logs justification.
        """
        caller.require_auth()
        self._require_initialized()
        
        proj_key = "proj:" + str(project_id)
        if not self.storage.has(proj_key):
            raise ContractError.PROJECT_NOT_FOUND
            
        if amount == U128(0):
            raise ContractError.INVALID_AMOUNT
            
        src_bal_key = "bal:" + str(caller)
        src_bal = self.storage.get(src_bal_key, U128(0))
        if src_bal < amount:
            raise ContractError.INSUFFICIENT_BALANCE
            
        # Deduct active credits
        self.storage.set(src_bal_key, src_bal - amount)
        
        # Increase retired logs
        ret_bal_key = "ret_bal:" + str(caller)
        ret_bal = self.storage.get(ret_bal_key, U128(0))
        self.storage.set(ret_bal_key, ret_bal + amount)
        
        # Record entry inside registry
        ret_id = self.storage.get("retirement_count", U64(0))
        reg_key = "ret_reg:" + str(ret_id)
        
        self.storage.set(reg_key + ":retirer", caller)
        self.storage.set(reg_key + ":project_id", project_id)
        self.storage.set(reg_key + ":amount", amount)
        self.storage.set(reg_key + ":time", self.env.ledger().timestamp())
        self.storage.set(reg_key + ":justification", justification_hash)
        
        self.storage.set("retirement_count", ret_id + U64(1))
        
        self.env.emit_event(
            "credits_retired", 
            {"retirer": caller, "project_id": project_id, "amount": amount, "ret_id": ret_id}
        )
        return True

    @view
    def get_balance(self, user: Address) -> U128:
        """
        Returns active tradable balance.
        """
        self._require_initialized()
        return self.storage.get("bal:" + str(user), U128(0))

    @view
    def get_retired_balance(self, user: Address) -> U128:
        """
        Returns total retired credits.
        """
        self._require_initialized()
        return self.storage.get("ret_bal:" + str(user), U128(0))

    @view
    def get_project_details(self, project_id: Symbol) -> Map:
        """
        Returns metadata of a registered carbon project.
        """
        self._require_initialized()
        proj_key = "proj:" + str(project_id)
        if not self.storage.has(proj_key):
            raise ContractError.PROJECT_NOT_FOUND
            
        details = Map()
        details.set(Symbol("developer"), self.storage.get(proj_key + ":developer"))
        details.set(Symbol("type"), self.storage.get(proj_key + ":type"))
        details.set(Symbol("certified_tonnage"), self.storage.get(proj_key + ":tonnage"))
        details.set(Symbol("metrics_hash"), self.storage.get(proj_key + ":metrics_hash"))
        details.set(Symbol("minted_credits"), self.storage.get(proj_key + ":minted"))
        return details

    @view
    def get_retirement_record(self, record_id: U64) -> Map:
        """
        Queries details of a specific retirement event.
        """
        self._require_initialized()
        count = self.storage.get("retirement_count", U64(0))
        if record_id >= count:
            raise ContractError.RECORD_NOT_FOUND
            
        reg_key = "ret_reg:" + str(record_id)
        record = Map()
        record.set(Symbol("retirer"), self.storage.get(reg_key + ":retirer"))
        record.set(Symbol("project_id"), self.storage.get(reg_key + ":project_id"))
        record.set(Symbol("amount"), self.storage.get(reg_key + ":amount"))
        record.set(Symbol("timestamp"), self.storage.get(reg_key + ":time"))
        record.set(Symbol("justification_hash"), self.storage.get(reg_key + ":justification"))
        return record

    @view
    def get_retirement_count(self) -> U64:
        """
        Returns the total number of retired offsets.
        """
        self._require_initialized()
        return self.storage.get("retirement_count", U64(0))

    # Internal helpers
    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED
