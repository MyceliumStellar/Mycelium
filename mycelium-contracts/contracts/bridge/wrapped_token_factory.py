"""
Wrapped Token Factory — Metadata registry and mint/burn control for foreign assets.

Mycelium Smart Contract for Stellar. Registers local tokens mapping to foreign
assets. Configures administrative minting and burning permissions for authorized
bridges, ensuring controlled supply management of bridged assets.
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
    MINT_PERMISSION_DENIED = 6
    BURN_PERMISSION_DENIED = 7
    INVALID_METADATA = 8
    PAUSED = 9

@contract
class WrappedTokenFactory:
    """
    Registry and authorization controller for wrapped foreign assets on Stellar.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """Initialize contract admin."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin})

    @external
    def register_wrapped_token(
        self,
        caller: Address,
        foreign_chain: Bytes,
        foreign_token: Bytes,
        local_token: Address,
        name: Symbol,
        symbol: Symbol,
        decimals: U64
    ):
        """
        Register a local token address to map to a foreign chain asset.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        # Check if already registered
        key = self._get_foreign_key(foreign_chain, foreign_token)
        if self.storage.get(f"token_ref_{key}") is not None:
            raise ContractError.TOKEN_ALREADY_REGISTERED

        # Record registration mapping
        self.storage.set(f"token_ref_{key}", local_token)
        self.storage.set(f"is_registered_{local_token}", True)
        
        # Save metadata
        self.storage.set(f"meta_name_{local_token}", name)
        self.storage.set(f"meta_symbol_{local_token}", symbol)
        self.storage.set(f"meta_decimals_{local_token}", decimals)
        self.storage.set(f"meta_foreign_chain_{local_token}", foreign_chain)
        self.storage.set(f"meta_foreign_token_{local_token}", foreign_token)

        self.env.emit_event("wrapped_token_registered", {
            "foreign_chain": foreign_chain,
            "foreign_token": foreign_token,
            "local_token": local_token,
            "name": name,
            "symbol": symbol
        })

    @external
    def set_permissions(
        self,
        caller: Address,
        bridge: Address,
        local_token: Address,
        can_mint: Bool,
        can_burn: Bool
    ):
        """
        Grant or revoke mint/burn privileges to a bridge contract for a registered wrapped token.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        if not self.storage.get(f"is_registered_{local_token}", False):
            raise ContractError.TOKEN_NOT_REGISTERED

        self.storage.set(f"allow_mint_{bridge}_{local_token}", can_mint)
        self.storage.set(f"allow_burn_{bridge}_{local_token}", can_burn)

        self.env.emit_event("permissions_updated", {
            "bridge": bridge,
            "local_token": local_token,
            "can_mint": can_mint,
            "can_burn": can_burn
        })

    @external
    def mint(self, caller: Address, local_token: Address, recipient: Address, amount: U128):
        """
        Mint wrapped tokens. Can only be called by a bridge with minting permissions.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if not self.storage.get(f"allow_mint_{caller}_{local_token}", False):
            raise ContractError.MINT_PERMISSION_DENIED

        # In Stellar/Soroban, a factory contract would call 'mint' on the token contract
        # specifying the recipient and amount. The factory must be the owner/admin of local_token.
        self.env.call(local_token, "mint", recipient, amount)

        self.env.emit_event("wrapped_minted", {
            "local_token": local_token,
            "recipient": recipient,
            "amount": amount,
            "bridge": caller
        })

    @external
    def burn(self, caller: Address, local_token: Address, from_addr: Address, amount: U128):
        """
        Burn wrapped tokens. Can only be called by a bridge with burning permissions.
        """
        caller.require_auth()
        self._require_initialized()

        if not self.storage.get(f"allow_burn_{caller}_{local_token}", False):
            raise ContractError.BURN_PERMISSION_DENIED

        # Burn tokens from owner address
        self.env.call(local_token, "burn", from_addr, amount)

        self.env.emit_event("wrapped_burned", {
            "local_token": local_token,
            "from": from_addr,
            "amount": amount,
            "bridge": caller
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause or unpause wrapped token minting (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_wrapped_token(self, foreign_chain: Bytes, foreign_token: Bytes) -> Address:
        """Query local token address mapped to a foreign asset."""
        key = self._get_foreign_key(foreign_chain, foreign_token)
        return self.storage.get(f"token_ref_{key}")

    @view
    def get_metadata(self, local_token: Address) -> Map:
        """Fetch metadata for a registered wrapped token."""
        res = Map(self.env)
        if self.storage.get(f"is_registered_{local_token}", False):
            res.set("name", self.storage.get(f"meta_name_{local_token}"))
            res.set("symbol", self.storage.get(f"meta_symbol_{local_token}"))
            res.set("decimals", self.storage.get(f"meta_decimals_{local_token}"))
            res.set("foreign_chain", self.storage.get(f"meta_foreign_chain_{local_token}"))
            res.set("foreign_token", self.storage.get(f"meta_foreign_token_{local_token}"))
        return res

    @view
    def get_permissions(self, bridge: Address, local_token: Address) -> Map:
        """Retrieve mint/burn allowances of a bridge contract."""
        res = Map(self.env)
        res.set("can_mint", self.storage.get(f"allow_mint_{bridge}_{local_token}", False))
        res.set("can_burn", self.storage.get(f"allow_burn_{bridge}_{local_token}", False))
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

    def _get_foreign_key(self, foreign_chain: Bytes, foreign_token: Bytes) -> Bytes:
        """Generate a unique lookup key for a foreign asset."""
        # Simple concat for lookup indexing
        return foreign_chain + foreign_token
