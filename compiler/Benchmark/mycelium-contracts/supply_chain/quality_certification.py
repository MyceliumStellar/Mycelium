"""
QualityCertification — Inspector registrations, certificate hashes, validation periods.

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
    INSPECTOR_ALREADY_REGISTERED = 4
    INSPECTOR_NOT_FOUND = 5
    CERTIFICATE_ALREADY_EXISTS = 6
    CERTIFICATE_NOT_FOUND = 7
    CERTIFICATE_EXPIRED = 8
    CERTIFICATE_REVOKED = 9

@contract
class QualityCertification:
    """
    Quality certification and audit log registry for supply chains.
    
    Manages inspector credentials, issues and validates quality certificate
    signatures/hashes, and tracks expiry schedules.
    """
    
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """
        Initializes the quality certification registry.
        
        Args:
            admin: Admin address controlling inspector registrations.
        """
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED
            
        self.storage.set("admin", admin)
        self.storage.set("initialized", True)
        
        self.env.emit_event("initialized", {"admin": admin})

    @external
    def register_inspector(self, caller: Address, inspector: Address, specialty: Symbol) -> Bool:
        """
        Authorizes a new quality inspector.
        
        Args:
            caller: Admin address.
            inspector: Auditor address to authorize.
            specialty: Category specialty (e.g. Symbol("Organic")).
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        insp_key = "insp:" + str(inspector)
        if self.storage.has(insp_key):
            raise ContractError.INSPECTOR_ALREADY_REGISTERED
            
        self.storage.set(insp_key, specialty)
        self.env.emit_event("inspector_registered", {"inspector": inspector, "specialty": specialty})
        return True

    @external
    def revoke_inspector(self, caller: Address, inspector: Address) -> Bool:
        """
        Revokes quality inspector credentials.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        insp_key = "insp:" + str(inspector)
        if not self.storage.has(insp_key):
            raise ContractError.INSPECTOR_NOT_FOUND
            
        self.storage.remove(insp_key)
        self.env.emit_event("inspector_revoked", {"inspector": inspector})
        return True

    @external
    def issue_certificate(
        self, 
        caller: Address, 
        cert_id: Symbol, 
        serial_number: Symbol, 
        cert_hash: Bytes, 
        validity_period: U64
    ) -> Bool:
        """
        Issues a quality certificate for a specific product.
        
        Args:
            caller: Registered inspector address.
            cert_id: Unique certification code.
            serial_number: Target product serial number.
            cert_hash: Cryptographic certificate document hash.
            validity_period: Validity duration in seconds.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_inspector(caller)
        
        cert_key = "cert:" + str(cert_id)
        if self.storage.has(cert_key):
            raise ContractError.CERTIFICATE_ALREADY_EXISTS
            
        issue_time = self.env.ledger().timestamp()
        expiry_time = issue_time + validity_period
        
        # Save details
        self.storage.set(cert_key, cert_hash)
        self.storage.set("c_issuer:" + str(cert_id), caller)
        self.storage.set("c_serial:" + str(cert_id), serial_number)
        self.storage.set("c_issue:" + str(cert_id), issue_time)
        self.storage.set("c_expiry:" + str(cert_id), expiry_time)
        self.storage.set("c_valid:" + str(cert_id), True)
        
        # Append cert to product certificate list
        prod_cert_key = "prod_certs:" + str(serial_number)
        prod_certs = self.storage.get(prod_cert_key, Vec())
        prod_certs.append(cert_id)
        self.storage.set(prod_cert_key, prod_certs)
        
        self.env.emit_event(
            "certificate_issued", 
            {
                "cert_id": cert_id, 
                "serial_number": serial_number, 
                "issuer": caller, 
                "expiry": expiry_time
            }
        )
        return True

    @external
    def revoke_certificate(self, caller: Address, cert_id: Symbol, reason: Symbol) -> Bool:
        """
        Revokes a quality certificate.
        
        Allowed for the original issuing inspector or contract admin.
        """
        caller.require_auth()
        self._require_initialized()
        
        cert_key = "cert:" + str(cert_id)
        if not self.storage.has(cert_key):
            raise ContractError.CERTIFICATE_NOT_FOUND
            
        issuer = self.storage.get("c_issuer:" + str(cert_id))
        admin = self.storage.get("admin")
        if caller != issuer and caller != admin:
            raise ContractError.UNAUTHORIZED
            
        self.storage.set("c_valid:" + str(cert_id), False)
        
        self.env.emit_event("certificate_revoked", {"cert_id": cert_id, "reason": reason})
        return True

    @view
    def verify_certificate(self, cert_id: Symbol) -> Bool:
        """
        Validates if a certificate is active, unrevoked, and within its validity window.
        """
        self._require_initialized()
        cert_key = "cert:" + str(cert_id)
        if not self.storage.has(cert_key):
            raise ContractError.CERTIFICATE_NOT_FOUND
            
        if not self.storage.get("c_valid:" + str(cert_id), False):
            raise ContractError.CERTIFICATE_REVOKED
            
        current_time = self.env.ledger().timestamp()
        expiry_time = self.storage.get("c_expiry:" + str(cert_id), U64(0))
        if current_time >= expiry_time:
            raise ContractError.CERTIFICATE_EXPIRED
            
        return True

    @view
    def get_certificate_details(self, cert_id: Symbol) -> Map:
        """
        Retrieves all parameters of a certificate.
        """
        self._require_initialized()
        cert_key = "cert:" + str(cert_id)
        if not self.storage.has(cert_key):
            raise ContractError.CERTIFICATE_NOT_FOUND
            
        details = Map()
        details.set(Symbol("hash"), self.storage.get(cert_key))
        details.set(Symbol("issuer"), self.storage.get("c_issuer:" + str(cert_id)))
        details.set(Symbol("serial_number"), self.storage.get("c_serial:" + str(cert_id)))
        details.set(Symbol("issue_date"), self.storage.get("c_issue:" + str(cert_id)))
        details.set(Symbol("expiry_date"), self.storage.get("c_expiry:" + str(cert_id)))
        details.set(Symbol("is_valid"), self.storage.get("c_valid:" + str(cert_id)))
        return details

    @view
    def get_product_certificates(self, serial_number: Symbol) -> Vec:
        """
        Returns a list of all certificate IDs issued to a product.
        """
        self._require_initialized()
        return self.storage.get("prod_certs:" + str(serial_number), Vec())

    @view
    def get_inspector_specialty(self, inspector: Address) -> Symbol:
        """
        Returns the specialty of a registered inspector.
        """
        self._require_initialized()
        insp_key = "insp:" + str(inspector)
        if not self.storage.has(insp_key):
            raise ContractError.INSPECTOR_NOT_FOUND
        return self.storage.get(insp_key)

    # Internal helpers
    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_inspector(self, caller: Address):
        insp_key = "insp:" + str(caller)
        if not self.storage.has(insp_key):
            raise ContractError.UNAUTHORIZED
