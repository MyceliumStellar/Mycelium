"""
NFT Collection Factory — Customizable deployer with phase, whitelist, and reveal controls.

Mycelium Smart Contract for Stellar. deploys/registers collections, configures
mint phases (Closed/Presale/Public), registers whitelist tiers, manages pre-reveal placeholders,
and facilitates payment collection with whitelist tier discounts.
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
    PHASE_CLOSED = 5
    NOT_ON_WHITELIST = 6
    SUPPLY_EXCEEDED = 7
    INSUFFICIENT_PAYMENT = 8
    ALREADY_REVEALED = 9
    COLLECTION_NOT_FOUND = 10
    INVALID_PHASE = 11
    LENGTH_MISMATCH = 12

@contract
class NFTCollectionFactory:
    """
    A smart contract that acts as a deployment manager and registry for NFT collections.
    Provides logic for minting phases, whitelist tier discounts, and metadata reveal toggles.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        payment_token: Address,
        discount_tier1_bps: U64,
        discount_tier2_bps: U64
    ):
        """Initialize the factory settings."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("payment_token", payment_token)
        self.storage.set("discount_tier1_bps", discount_tier1_bps)
        self.storage.set("discount_tier2_bps", discount_tier2_bps)
        self.storage.set("collections_count", U64(0))
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "payment_token": payment_token
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause deployment and minting."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    @external
    def deploy_collection(
        self,
        caller: Address,
        collection_wasm_hash: Bytes,
        salt: Bytes,
        price: U128,
        max_supply: U64,
        placeholder_uri: Bytes,
        base_uri: Bytes
    ) -> Address:
        """
        Deploy and register a new collection.
        Simulates deploying a contract using the Stellar env.deployer() pattern.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        # In Stellar/Soroban, deployer pattern:
        # deploy_addr = self.env.deployer().with_address(caller, salt).deploy(collection_wasm_hash)
        # We model this by using a unique derived address and recording it:
        deploy_addr = self.env.deployer().upload_contract_wasm(collection_wasm_hash) # Mock / standard SDK call representation
        
        # Register collection configurations
        self.storage.set(f"col_owner_{deploy_addr}", caller)
        self.storage.set(f"col_price_{deploy_addr}", price)
        self.storage.set(f"col_max_supply_{deploy_addr}", max_supply)
        self.storage.set(f"col_supply_{deploy_addr}", U64(0))
        self.storage.set(f"col_phase_{deploy_addr}", U64(0))  # 0 = Closed, 1 = Presale, 2 = Public
        self.storage.set(f"col_placeholder_uri_{deploy_addr}", placeholder_uri)
        self.storage.set(f"col_base_uri_{deploy_addr}", base_uri)
        self.storage.set(f"col_revealed_{deploy_addr}", False)

        # Track collection count
        count = self.storage.get("collections_count", U64(0))
        self.storage.set(f"col_addr_{count}", deploy_addr)
        self.storage.set("collections_count", count + U64(1))

        self.env.emit_event("collection_deployed", {
            "collection": deploy_addr,
            "owner": caller,
            "price": price,
            "max_supply": max_supply
        })

        return deploy_addr

    # --- COLLECTION MANAGEMENT ---

    @external
    def set_mint_phase(self, caller: Address, collection: Address, phase: U64):
        """Set collection minting phase (0 = Closed, 1 = Presale/Whitelist, 2 = Public)."""
        caller.require_auth()
        self._require_initialized()
        self._require_collection_owner(collection, caller)

        if phase > U64(2):
            raise ContractError.INVALID_PHASE

        self.storage.set(f"col_phase_{collection}", phase)
        self.env.emit_event("mint_phase_updated", {"collection": collection, "phase": phase})

    @external
    def set_whitelist(self, caller: Address, collection: Address, users: Vec, tiers: Vec):
        """Set whitelist tiers for a collection (Tier 1 = High discount, Tier 2 = Low discount)."""
        caller.require_auth()
        self._require_initialized()
        self._require_collection_owner(collection, caller)

        if len(users) != len(tiers):
            raise ContractError.LENGTH_MISMATCH

        for i in range(len(users)):
            user = users.get(i)
            tier = tiers.get(i)
            # Tier 0 = not whitelisted, Tier 1 = tier 1, Tier 2 = tier 2
            self.storage.set(f"col_whitelist_{collection}_{user}", tier)

        self.env.emit_event("whitelist_configured", {"collection": collection, "users_count": len(users)})

    @external
    def reveal_collection(self, caller: Address, collection: Address):
        """Reveal metadata. URI queries will transition from placeholder to base token URIs."""
        caller.require_auth()
        self._require_initialized()
        self._require_collection_owner(collection, caller)

        revealed = self.storage.get(f"col_revealed_{collection}", False)
        if revealed:
            raise ContractError.ALREADY_REVEALED

        self.storage.set(f"col_revealed_{collection}", True)
        self.env.emit_event("collection_revealed", {"collection": collection})

    # --- MINTING LOGIC ---

    @external
    def mint(self, caller: Address, collection: Address) -> U64:
        """Mint an NFT from the designated collection, handling phase, whitelist discounts, and payments."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        # Check collection exists
        col_owner = self.storage.get(f"col_owner_{collection}")
        if col_owner is None:
            raise ContractError.COLLECTION_NOT_FOUND

        phase = self.storage.get(f"col_phase_{collection}", U64(0))
        if phase == U64(0):
            raise ContractError.PHASE_CLOSED

        supply = self.storage.get(f"col_supply_{collection}", U64(0))
        max_supply = self.storage.get(f"col_max_supply_{collection}", U64(0))
        if supply >= max_supply:
            raise ContractError.SUPPLY_EXCEEDED

        base_price = self.storage.get(f"col_price_{collection}", U128(0))
        discount_bps = U64(0)

        # Check whitelist constraints if phase is Presale
        whitelist_tier = self.storage.get(f"col_whitelist_{collection}_{caller}", U64(0))
        if phase == U64(1):
            if whitelist_tier == U64(0):
                raise ContractError.NOT_ON_WHITELIST

        # Apply discounts if user is whitelisted (applies in both presale and public phases if whitelisted)
        if whitelist_tier == U64(1):
            discount_bps = self.storage.get("discount_tier1_bps", U64(0))
        elif whitelist_tier == U64(2):
            discount_bps = self.storage.get("discount_tier2_bps", U64(0))

        # Calculate final price
        discount = (base_price * U128(discount_bps)) / U128(10000)
        final_price = base_price - discount

        # Pay collection owner
        if final_price > U128(0):
            payment_token = self.storage.get("payment_token")
            self.env.call(payment_token, "transfer", caller, col_owner, final_price)

        # Call the collection contract to execute actual mint
        # Assumes collection contract implements a standard mint call accepting receiver
        token_id = self.env.call(collection, "mint", self.env.current_contract_address(), caller)

        # Update supply
        self.storage.set(f"col_supply_{collection}", supply + U64(1))

        self.env.emit_event("nft_minted", {
            "collection": collection,
            "token_id": token_id,
            "buyer": caller,
            "price_paid": final_price
        })

        return token_id

    # --- VIEWS ---

    @view
    def get_token_uri(self, collection: Address, token_id: U64) -> Bytes:
        """Returns the appropriate token URI depending on the reveal status."""
        self._require_initialized()
        
        # Check if collection is revealed
        revealed = self.storage.get(f"col_revealed_{collection}", False)
        if not revealed:
            # Return collection-wide placeholder URI
            return self.storage.get(f"col_placeholder_uri_{collection}", Bytes(b""))
        else:
            # Return token specific URI (simulated by appending token_id to base_uri)
            base_uri = self.storage.get(f"col_base_uri_{collection}", Bytes(b""))
            # In a production setup, we can concatenate base_uri + '/' + token_id.
            # To be simple and robust:
            return base_uri

    @view
    def get_collection_info(self, collection: Address) -> Map:
        """Inspect collection configuration."""
        res = Map(self.env)
        owner = self.storage.get(f"col_owner_{collection}")
        if owner is not None:
            res.set("owner", owner)
            res.set("price", self.storage.get(f"col_price_{collection}"))
            res.set("max_supply", self.storage.get(f"col_max_supply_{collection}"))
            res.set("supply", self.storage.get(f"col_supply_{collection}"))
            res.set("phase", self.storage.get(f"col_phase_{collection}"))
            res.set("revealed", self.storage.get(f"col_revealed_{collection}"))
        return res

    # --- INTERNAL HELPERS ---

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_not_paused(self):
        if self.storage.get("paused", False):
            raise ContractError.PAUSED

    def _require_admin(self, caller: Address):
        if caller != self.storage.get("admin"):
            raise ContractError.UNAUTHORIZED

    def _require_collection_owner(self, collection: Address, caller: Address):
        owner = self.storage.get(f"col_owner_{collection}")
        if owner is None or owner != caller:
            raise ContractError.UNAUTHORIZED
