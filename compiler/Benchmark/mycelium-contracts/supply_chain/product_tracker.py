"""
ProductTracker — Product serial tracing, provenance history logs, transition approvals.

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
    PRODUCT_ALREADY_EXISTS = 4
    PRODUCT_NOT_FOUND = 5
    TRANSFER_NOT_APPROVED = 6
    INVALID_SERIAL = 7

@contract
class ProductTracker:
    """
    Provenance tracking and custody registry for supply chain products.
    
    Traces unique product serial numbers from registration through custody hops,
    storing locations, timestamps, and inspector audits.
    """
    
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """
        Initializes the tracker contract.
        
        Args:
            admin: Admin address controlling registration authorizations.
        """
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED
            
        self.storage.set("admin", admin)
        self.storage.set("initialized", True)
        
        self.env.emit_event("initialized", {"admin": admin})

    @external
    def register_product(
        self, 
        caller: Address, 
        serial_number: Symbol, 
        initial_location: Symbol
    ) -> Bool:
        """
        Registers a new product tracking entity.
        
        Args:
            caller: Owner/manufacturer address.
            serial_number: Unique product ID.
            initial_location: Origin location of the product.
        """
        caller.require_auth()
        self._require_initialized()
        
        if len(str(serial_number)) == 0:
            raise ContractError.INVALID_SERIAL
            
        owner_key = "prod_owner:" + str(serial_number)
        if self.storage.has(owner_key):
            raise ContractError.PRODUCT_ALREADY_EXISTS
            
        current_time = self.env.ledger().timestamp()
        
        # Save initial owner and state
        self.storage.set(owner_key, caller)
        self.storage.set("prod_count:" + str(serial_number), U64(1))
        
        # Add initial provenance log entry
        log_key = "prod_log:" + str(serial_number) + ":0"
        self.storage.set(log_key + ":custodian", caller)
        self.storage.set(log_key + ":location", initial_location)
        self.storage.set(log_key + ":status", Symbol("REGISTERED"))
        self.storage.set(log_key + ":timestamp", current_time)
        self.storage.set(log_key + ":details", Bytes())
        
        self.env.emit_event(
            "product_registered", 
            {
                "serial_number": serial_number, 
                "manufacturer": caller, 
                "location": initial_location, 
                "timestamp": current_time
            }
        )
        return True

    @external
    def approve_transfer(self, caller: Address, serial_number: Symbol, receiver: Address) -> Bool:
        """
        Approves another account to claim custody of the product.
        
        Args:
            caller: The current custodian.
            serial_number: Product ID.
            receiver: Approved recipient.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_product_exists(serial_number)
        
        current_owner = self.storage.get("prod_owner:" + str(serial_number))
        if caller != current_owner:
            raise ContractError.UNAUTHORIZED
            
        self.storage.set("prod_appr:" + str(serial_number), receiver)
        
        self.env.emit_event(
            "transfer_approved", 
            {"serial_number": serial_number, "from": caller, "to": receiver}
        )
        return True

    @external
    def claim_custody(self, caller: Address, serial_number: Symbol, new_location: Symbol) -> Bool:
        """
        Claims custody of the product after receiving authorization.
        
        Args:
            caller: Approved recipient address claiming the product.
            serial_number: Product ID.
            new_location: Target destination of the product.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_product_exists(serial_number)
        
        # Check authorization
        approved = self.storage.get("prod_appr:" + str(serial_number))
        if caller != approved:
            raise ContractError.TRANSFER_NOT_APPROVED
            
        current_owner = self.storage.get("prod_owner:" + str(serial_number))
        current_time = self.env.ledger().timestamp()
        
        # Update Ownership
        self.storage.set("prod_owner:" + str(serial_number), caller)
        self.storage.remove("prod_appr:" + str(serial_number))
        
        # Log entry addition
        count = self.storage.get("prod_count:" + str(serial_number), U64(0))
        log_key = "prod_log:" + str(serial_number) + ":" + str(count)
        
        self.storage.set(log_key + ":custodian", caller)
        self.storage.set(log_key + ":location", new_location)
        self.storage.set(log_key + ":status", Symbol("TRANSFERRED"))
        self.storage.set(log_key + ":timestamp", current_time)
        self.storage.set(log_key + ":details", Bytes())
        
        self.storage.set("prod_count:" + str(serial_number), count + U64(1))
        
        self.env.emit_event(
            "custody_transferred", 
            {
                "serial_number": serial_number, 
                "from": current_owner, 
                "to": caller, 
                "location": new_location, 
                "timestamp": current_time
            }
        )
        return True

    @external
    def add_audit_record(
        self, 
        caller: Address, 
        serial_number: Symbol, 
        status: Symbol, 
        audit_details: Bytes
    ) -> Bool:
        """
        Allows inspectors or administrators to add audit checks onto a product.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self._require_product_exists(serial_number)
        
        current_owner = self.storage.get("prod_owner:" + str(serial_number))
        location_key = "prod_log:" + str(serial_number) + ":" + str(self.storage.get("prod_count:" + str(serial_number), U64(1)) - U64(1))
        current_location = self.storage.get(location_key + ":location")
        current_time = self.env.ledger().timestamp()
        
        # Append audit block as new index entry
        count = self.storage.get("prod_count:" + str(serial_number), U64(0))
        log_key = "prod_log:" + str(serial_number) + ":" + str(count)
        
        self.storage.set(log_key + ":custodian", current_owner)
        self.storage.set(log_key + ":location", current_location)
        self.storage.set(log_key + ":status", status)
        self.storage.set(log_key + ":timestamp", current_time)
        self.storage.set(log_key + ":details", audit_details)
        
        self.storage.set("prod_count:" + str(serial_number), count + U64(1))
        
        self.env.emit_event(
            "product_audited", 
            {"serial_number": serial_number, "auditor": caller, "status": status, "timestamp": current_time}
        )
        return True

    @view
    def get_current_owner(self, serial_number: Symbol) -> Address:
        """
        Queries the current custodian of a product.
        """
        self._require_initialized()
        self._require_product_exists(serial_number)
        return self.storage.get("prod_owner:" + str(serial_number))

    @view
    def get_provenance_count(self, serial_number: Symbol) -> U64:
        """
        Returns the history count of a product.
        """
        self._require_initialized()
        self._require_product_exists(serial_number)
        return self.storage.get("prod_count:" + str(serial_number), U64(0))

    @view
    def get_provenance_entry(self, serial_number: Symbol, index: U64) -> Map:
        """
        Returns log details at a specific point in a product's provenance history.
        """
        self._require_initialized()
        self._require_product_exists(serial_number)
        
        count = self.storage.get("prod_count:" + str(serial_number), U64(0))
        if index >= count:
            raise ContractError.PRODUCT_NOT_FOUND
            
        log_key = "prod_log:" + str(serial_number) + ":" + str(index)
        
        details = Map()
        details.set(Symbol("custodian"), self.storage.get(log_key + ":custodian"))
        details.set(Symbol("location"), self.storage.get(log_key + ":location"))
        details.set(Symbol("status"), self.storage.get(log_key + ":status"))
        details.set(Symbol("timestamp"), self.storage.get(log_key + ":timestamp"))
        details.set(Symbol("details"), self.storage.get(log_key + ":details"))
        return details

    # Internal helpers
    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_product_exists(self, serial_number: Symbol):
        owner_key = "prod_owner:" + str(serial_number)
        if not self.storage.has(owner_key):
            raise ContractError.PRODUCT_NOT_FOUND
