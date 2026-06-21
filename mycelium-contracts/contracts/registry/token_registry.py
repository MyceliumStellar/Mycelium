"""
TokenRegistry — Metadata registration, decimals check, compliance verification.

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
    TOKEN_ALREADY_REGISTERED = 4
    TOKEN_NOT_REGISTERED = 5
    INVALID_DECIMALS = 6
    INVALID_METADATA = 7
    COMPLIANCE_FAILED = 8
    BLACK_LISTED = 9
    PAUSED = 10

@contract
class TokenRegistry:
    """
    Registry for token metadata and compliance checks.
    
    Allows registering tokens, enforcing decimal rules (0 to 18), 
    and auditing transaction compliance against active blacklist policies.
    """
    
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()
    
    @external
    def initialize(self, admin: Address):
        """
        Initializes the contract with an administrator.
        
        Args:
            admin: The administrator Address who controls registrations and compliance.
        """
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED
        
        self.storage.set("admin", admin)
        self.storage.set("initialized", True)
        self.storage.set("token_count", U64(0))
        self.storage.set("paused", False)
        
        self.env.emit_event("initialized", {"admin": admin})
    
    @external
    def register_token(
        self, 
        caller: Address, 
        token_address: Address, 
        name: Symbol, 
        symbol: Symbol, 
        decimals: U64, 
        issuer: Address, 
        compliance_required: Bool
    ) -> Bool:
        """
        Registers a new token inside the registry with verified metadata.
        
        Args:
            caller: The address requesting registration (must be admin).
            token_address: The address of the token contract.
            name: The descriptive name of the token.
            symbol: The ticker symbol.
            decimals: Precision decimals (must be <= 18).
            issuer: The issuer address.
            compliance_required: Flag indicating if compliance checks are active.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self._require_not_paused()
        
        # Check if already registered
        reg_key = "reg:" + str(token_address)
        if self.storage.get(reg_key, False):
            raise ContractError.TOKEN_ALREADY_REGISTERED
            
        # Decimal checks
        if decimals > U64(18):
            raise ContractError.INVALID_DECIMALS
            
        # Check metadata validity
        if len(str(name)) == 0 or len(str(symbol)) == 0:
            raise ContractError.INVALID_METADATA

        # Save metadata fields
        self.storage.set(reg_key, True)
        self.storage.set("name:" + str(token_address), name)
        self.storage.set("sym:" + str(token_address), symbol)
        self.storage.set("dec:" + str(token_address), decimals)
        self.storage.set("iss:" + str(token_address), issuer)
        self.storage.set("comp:" + str(token_address), compliance_required)
        
        # Track index mapping
        count = self.storage.get("token_count", U64(0))
        self.storage.set("t_idx:" + str(count), token_address)
        self.storage.set("token_count", count + U64(1))
        
        self.env.emit_event(
            "token_registered", 
            {
                "token": token_address, 
                "symbol": symbol, 
                "decimals": decimals, 
                "issuer": issuer
            }
        )
        return True
        
    @external
    def update_compliance(
        self, 
        caller: Address, 
        token_address: Address, 
        compliance_required: Bool
    ) -> Bool:
        """
        Enables or disables compliance requirements for a token.
        
        Args:
            caller: Administrator address.
            token_address: Token contract address.
            compliance_required: New compliance requirement state.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self._require_registered(token_address)
        
        self.storage.set("comp:" + str(token_address), compliance_required)
        self.env.emit_event(
            "compliance_updated", 
            {"token": token_address, "required": compliance_required}
        )
        return True

    @external
    def set_blacklisted(
        self, 
        caller: Address, 
        token_address: Address, 
        user_address: Address, 
        blacklisted: Bool
    ) -> Bool:
        """
        Blacklists or whitelists a user address for a specific token.
        
        Args:
            caller: Administrator address.
            token_address: Token contract address.
            user_address: Target address to restrict or restore.
            blacklisted: True to blacklist, False to whitelist.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self._require_registered(token_address)
        
        bl_key = "bl:" + str(token_address) + ":" + str(user_address)
        self.storage.set(bl_key, blacklisted)
        
        self.env.emit_event(
            "blacklist_changed", 
            {"token": token_address, "user": user_address, "blacklisted": blacklisted}
        )
        return True

    @view
    def verify_compliance(
        self, 
        token_address: Address, 
        sender: Address, 
        receiver: Address, 
        amount: U128
    ) -> Bool:
        """
        Performs compliance verification for transactions.
        
        Throws error if compliant state is violated, returns True otherwise.
        
        Args:
            token_address: Address of the token being transferred.
            sender: The transfer initiator address.
            receiver: The transfer destination address.
            amount: The transfer amount.
        """
        self._require_initialized()
        self._require_registered(token_address)
        
        compliance_required = self.storage.get("comp:" + str(token_address), False)
        if not compliance_required:
            return True
            
        # Check sender and receiver blacklists
        sender_bl = self.storage.get("bl:" + str(token_address) + ":" + str(sender), False)
        if sender_bl:
            raise ContractError.BLACK_LISTED
            
        receiver_bl = self.storage.get("bl:" + str(token_address) + ":" + str(receiver), False)
        if receiver_bl:
            raise ContractError.BLACK_LISTED
            
        # Optional: check non-zero transfer
        if amount == U128(0):
            raise ContractError.COMPLIANCE_FAILED
            
        return True

    @external
    def set_paused(self, caller: Address, paused: Bool) -> Bool:
        """
        Pauses or unpauses contract registration features.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        self.storage.set("paused", paused)
        self.env.emit_event("paused_state_changed", {"paused": paused})
        return True

    @view
    def get_token_metadata(self, token_address: Address) -> Map:
        """
        Returns all registered metadata for a given token address.
        """
        self._require_initialized()
        self._require_registered(token_address)
        
        meta = Map()
        meta.set(Symbol("name"), self.storage.get("name:" + str(token_address)))
        meta.set(Symbol("symbol"), self.storage.get("sym:" + str(token_address)))
        meta.set(Symbol("decimals"), self.storage.get("dec:" + str(token_address)))
        meta.set(Symbol("issuer"), self.storage.get("iss:" + str(token_address)))
        meta.set(Symbol("compliance_required"), self.storage.get("comp:" + str(token_address)))
        return meta

    @view
    def get_token_count(self) -> U64:
        """
        Returns the total number of registered tokens.
        """
        self._require_initialized()
        return self.storage.get("token_count", U64(0))

    @view
    def get_token_at_index(self, index: U64) -> Address:
        """
        Returns the token address at the specified index.
        """
        self._require_initialized()
        count = self.storage.get("token_count", U64(0))
        if index >= count:
            raise ContractError.TOKEN_NOT_REGISTERED
            
        return self.storage.get("t_idx:" + str(index))

    # Internal helpers
    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_registered(self, token_address: Address):
        reg_key = "reg:" + str(token_address)
        if not self.storage.get(reg_key, False):
            raise ContractError.TOKEN_NOT_REGISTERED

    def _require_not_paused(self):
        if self.storage.get("paused", False):
            raise ContractError.PAUSED
