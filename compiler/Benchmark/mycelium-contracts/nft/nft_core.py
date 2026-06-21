"""
NFT Core Contract — Basic NFT.

Mycelium Smart Contract for Stellar that implements basic NFT functionality including
sequential token IDs, minting, burning, approvals, transfer rules, and metadata URIs.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)

class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    NOT_FOUND = 3
    UNAUTHORIZED = 4
    INVALID_CALLER = 5
    ZERO_ADDRESS = 6
    SUPPLY_EXCEEDED = 7
    NOT_OWNER = 8
    ALREADY_OWNED = 9
    INVALID_INDEX = 10
    PAUSED = 11

@contract
class NFTCore:
    """A self-contained NFT contract with admin controls and standard NFT functionality."""
    
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()
    
    @external
    def initialize(self, admin: Address, name: Bytes, symbol: Bytes, max_supply: U64):
        """
        Initialize the contract state with admin and collection metadata.
        
        Args:
            admin: Address of the collection administrator.
            name: Collection name as Bytes.
            symbol: Collection symbol as Bytes.
            max_supply: Maximum possible tokens in the collection.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED
            
        self.storage.set("admin", admin)
        self.storage.set("name", name)
        self.storage.set("symbol", symbol)
        self.storage.set("max_supply", max_supply)
        self.storage.set("next_token_id", U64(1))
        self.storage.set("total_supply", U64(0))
        self.storage.set("paused", False)
        self.storage.set("initialized", True)
        
        self.env.emit_event("initialized", {
            "admin": admin,
            "name": name,
            "symbol": symbol,
            "max_supply": max_supply
        })
        
    @external
    def set_paused(self, caller: Address, paused: Bool):
        """
        Pause or unpause contract transfers and minting operations.
        
        Args:
            caller: The address of the caller, must be admin.
            paused: The new paused status.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        self.storage.set("paused", paused)
        self.env.emit_event("paused_updated", {"paused": paused})
        
    @external
    def mint(self, caller: Address, to: Address) -> U64:
        """
        Mint a new sequential NFT.
        
        Args:
            caller: The address requesting the mint.
            to: Address that will receive the minted NFT.
            
        Returns:
            The ID of the minted token.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()
        
        # Only admin is allowed to mint in this basic core setup
        self._require_admin(caller)
        
        next_id = self.storage.get("next_token_id", U64(1))
        max_supply = self.storage.get("max_supply", U64(0))
        
        if next_id > max_supply:
            raise ContractError.SUPPLY_EXCEEDED
            
        # Update mappings
        self.storage.set(f"owner_{next_id}", to)
        
        # Update balances
        curr_balance = self.storage.get(f"balance_{to}", U64(0))
        self.storage.set(f"balance_{to}", curr_balance + U64(1))
        
        # Update supply counts
        self.storage.set("next_token_id", next_id + U64(1))
        curr_supply = self.storage.get("total_supply", U64(0))
        self.storage.set("total_supply", curr_supply + U64(1))
        
        self.env.emit_event("transfer", {
            "from": caller, # Simulated zero address mapping or issuer
            "to": to,
            "token_id": next_id
        })
        
        return next_id

    @external
    def burn(self, caller: Address, token_id: U64):
        """
        Burn a token, removing it from existence.
        
        Args:
            caller: The caller address, must be the owner or authorized operator.
            token_id: The ID of the token to burn.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()
        
        owner = self._get_owner_or_raise(token_id)
        
        if caller != owner:
            is_approved = self.storage.get(f"approved_{token_id}") == caller
            is_operator = self.storage.get(f"operator_{owner}_{caller}", False)
            if not (is_approved or is_operator):
                raise ContractError.UNAUTHORIZED
                
        # Clean up approvals
        self.storage.remove(f"approved_{token_id}")
        
        # Decrement owner balance
        curr_balance = self.storage.get(f"balance_{owner}", U64(0))
        if curr_balance > U64(0):
            self.storage.set(f"balance_{owner}", curr_balance - U64(1))
            
        # Remove owner mapping
        self.storage.remove(f"owner_{token_id}")
        
        # Clean up URI
        self.storage.remove(f"uri_{token_id}")
        
        # Decrement supply
        curr_supply = self.storage.get("total_supply", U64(0))
        if curr_supply > U64(0):
            self.storage.set("total_supply", curr_supply - U64(1))
            
        self.env.emit_event("transfer", {
            "from": owner,
            "to": caller, # Burning indicates transferring to a null or burner state
            "token_id": token_id
        })
        self.env.emit_event("burned", {"token_id": token_id})

    @external
    def transfer(self, caller: Address, to: Address, token_id: U64):
        """
        Transfer ownership of a specific NFT.
        
        Args:
            caller: The caller, must be owner or authorized operator/approved.
            to: Recipient address.
            token_id: Token ID to transfer.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()
        
        owner = self._get_owner_or_raise(token_id)
        
        if caller != owner:
            is_approved = self.storage.get(f"approved_{token_id}") == caller
            is_operator = self.storage.get(f"operator_{owner}_{caller}", False)
            if not (is_approved or is_operator):
                raise ContractError.UNAUTHORIZED
                
        if owner == to:
            raise ContractError.ALREADY_OWNED
            
        # Update approvals
        self.storage.remove(f"approved_{token_id}")
        
        # Update balances
        owner_bal = self.storage.get(f"balance_{owner}", U64(0))
        if owner_bal > U64(0):
            self.storage.set(f"balance_{owner}", owner_bal - U64(1))
            
        to_bal = self.storage.get(f"balance_{to}", U64(0))
        self.storage.set(f"balance_{to}", to_bal + U64(1))
        
        # Update owner
        self.storage.set(f"owner_{token_id}", to)
        
        self.env.emit_event("transfer", {
            "from": owner,
            "to": to,
            "token_id": token_id
        })

    @external
    def approve(self, caller: Address, approved: Address, token_id: U64):
        """
        Approve an address to transfer a specific token.
        
        Args:
            caller: Must be owner or authorized operator.
            approved: The address being authorized.
            token_id: Token ID.
        """
        caller.require_auth()
        self._require_initialized()
        
        owner = self._get_owner_or_raise(token_id)
        
        if caller != owner:
            is_operator = self.storage.get(f"operator_{owner}_{caller}", False)
            if not is_operator:
                raise ContractError.UNAUTHORIZED
                
        self.storage.set(f"approved_{token_id}", approved)
        self.env.emit_event("approval", {
            "owner": owner,
            "approved": approved,
            "token_id": token_id
        })

    @external
    def set_approval_for_all(self, caller: Address, operator: Address, approved: Bool):
        """
        Approve or revoke operator privileges for caller's assets.
        
        Args:
            caller: Authorizing party.
            operator: Authorized operator.
            approved: Status flag.
        """
        caller.require_auth()
        self._require_initialized()
        
        if caller == operator:
            raise ContractError.INVALID_CALLER
            
        self.storage.set(f"operator_{caller}_{operator}", approved)
        self.env.emit_event("approval_for_all", {
            "owner": caller,
            "operator": operator,
            "approved": approved
        })

    @external
    def set_token_uri(self, caller: Address, token_id: U64, uri: Bytes):
        """
        Set a token-specific URI.
        
        Args:
            caller: Admin address.
            token_id: The token ID.
            uri: Metadata URI.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        # Check that token exists
        self._get_owner_or_raise(token_id)
        
        self.storage.set(f"uri_{token_id}", uri)
        self.env.emit_event("uri_updated", {
            "token_id": token_id,
            "uri": uri
        })

    @view
    def get_owner(self, token_id: U64) -> Address:
        """Get the owner of a token."""
        self._require_initialized()
        return self._get_owner_or_raise(token_id)

    @view
    def get_balance(self, owner: Address) -> U64:
        """Get token balance of an owner."""
        self._require_initialized()
        return self.storage.get(f"balance_{owner}", U64(0))

    @view
    def get_approved(self, token_id: U64) -> Address:
        """Get approved address for a token."""
        self._require_initialized()
        self._get_owner_or_raise(token_id)
        return self.storage.get(f"approved_{token_id}")

    @view
    def is_approved_for_all(self, owner: Address, operator: Address) -> Bool:
        """Check if operator is approved for all owner tokens."""
        self._require_initialized()
        return self.storage.get(f"operator_{owner}_{operator}", False)

    @view
    def get_token_uri(self, token_id: U64) -> Bytes:
        """Get metadata URI for token."""
        self._require_initialized()
        self._get_owner_or_raise(token_id)
        return self.storage.get(f"uri_{token_id}", Bytes(b""))

    @view
    def get_total_supply(self) -> U64:
        """Get total supply of tokens."""
        self._require_initialized()
        return self.storage.get("total_supply", U64(0))

    @view
    def get_max_supply(self) -> U64:
        """Get max supply limit."""
        self._require_initialized()
        return self.storage.get("max_supply", U64(0))

    # Helper methods
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

    def _get_owner_or_raise(self, token_id: U64) -> Address:
        owner = self.storage.get(f"owner_{token_id}")
        if owner is None:
            raise ContractError.NOT_FOUND
        return owner
