"""
Composable NFT — ERC998 Parent-Child NFT relations.

Mycelium Smart Contract for Stellar. Enables NFTs to own other NFTs
(both from this collection and external collections), enforces depth caps,
implements loop/cycle detection, and supports child transfers and batch attachments.
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
    TOKEN_NOT_FOUND = 5
    MAX_DEPTH_EXCEEDED = 6
    LOOP_DETECTED = 7
    CHILD_NOT_FOUND = 8
    SUPPLY_EXCEEDED = 9
    INVALID_OPERATION = 10
    ALREADY_ATTACHED = 11

@contract
class ComposableNFT:
    """
    A composable NFT collection allowing hierarchical structuring.
    Enforces a depth cap of 4, runs recursive cycle checks to prevent circular ownership,
    and enables batch attachments.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, max_supply: U64):
        """Initialize the collection settings."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("max_supply", max_supply)
        self.storage.set("next_token_id", U64(1))
        self.storage.set("total_supply", U64(0))
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin, "max_supply": max_supply})

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause composable actions."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    @external
    def mint(self, caller: Address, to: Address) -> U64:
        """Mint a base NFT."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()
        self._require_admin(caller)

        next_id = self.storage.get("next_token_id", U64(1))
        max_supply = self.storage.get("max_supply", U64(0))
        if next_id > max_supply:
            raise ContractError.SUPPLY_EXCEEDED

        # Setup standard ownership (parent is 0, owner address is registered)
        self.storage.set(f"parent_{next_id}", U64(0))
        self.storage.set(f"owner_address_{next_id}", to)

        self.storage.set("next_token_id", next_id + U64(1))
        curr_supply = self.storage.get("total_supply", U64(0))
        self.storage.set("total_supply", curr_supply + U64(1))

        self.env.emit_event("minted", {"token_id": next_id, "to": to})
        return next_id

    # --- COMPOSABLE CORE OPERATIONS ---

    @external
    def attach_child_token(self, caller: Address, parent_id: U64, child_id: U64):
        """
        Attach an internal token (minted from this contract) as a child of another.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        # Check existence
        self._require_exists(parent_id)
        self._require_exists(child_id)

        if parent_id == child_id:
            raise ContractError.LOOP_DETECTED

        # Ensure caller is owner/root owner of both parent and child
        root_parent_owner = self._get_root_owner(parent_id)
        root_child_owner = self._get_root_owner(child_id)

        if caller != root_child_owner or caller != root_parent_owner:
            raise ContractError.UNAUTHORIZED

        # Prevent circular ownership and enforce nesting depth limits
        self._detect_loop_and_depth(child_id, parent_id)

        # Update child parent reference
        self.storage.set(f"parent_{child_id}", parent_id)
        self.storage.remove(f"owner_address_{child_id}") # No longer owned directly by an Address

        self.env.emit_event("child_token_attached", {
            "parent_id": parent_id,
            "child_id": child_id,
            "operator": caller
        })

    @external
    def detach_child_token(self, caller: Address, parent_id: U64, child_id: U64, recipient: Address):
        """
        Detach an internal token from its parent and transfer direct ownership to recipient.
        """
        caller.require_auth()
        self._require_initialized()

        self._require_exists(parent_id)
        self._require_exists(child_id)

        # Verify parent-child relationship
        curr_parent = self.storage.get(f"parent_{child_id}", U64(0))
        if curr_parent != parent_id:
            raise ContractError.CHILD_NOT_FOUND

        # Only root owner of parent can detach child
        root_owner = self._get_root_owner(parent_id)
        if caller != root_owner:
            raise ContractError.UNAUTHORIZED

        # Set parent to 0, write recipient address
        self.storage.set(f"parent_{child_id}", U64(0))
        self.storage.set(f"owner_address_{child_id}", recipient)

        self.env.emit_event("child_token_detached", {
            "parent_id": parent_id,
            "child_id": child_id,
            "recipient": recipient
        })

    # --- EXTERNAL CONTRACT ATTACHMENTS (ERC998 style) ---

    @external
    def attach_external_child(self, caller: Address, parent_id: U64, child_contract: Address, child_token_id: U64):
        """
        Attach an NFT from another contract as a child of parent_id.
        Escrows the external child NFT into this contract.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()
        self._require_exists(parent_id)

        # Verify caller is root owner of parent
        root_owner = self._get_root_owner(parent_id)
        if caller != root_owner:
            raise ContractError.UNAUTHORIZED

        key_prefix = f"ext_{parent_id}_{child_contract}_{child_token_id}"
        if self.storage.get(f"{key_prefix}_owned", False):
            raise ContractError.ALREADY_ATTACHED

        # Escrow external NFT here
        self.env.call(child_contract, "transfer", caller, self.env.current_contract_address(), child_token_id)

        # Record attachment status
        self.storage.set(f"{key_prefix}_owned", True)

        self.env.emit_event("external_child_attached", {
            "parent_id": parent_id,
            "child_contract": child_contract,
            "child_token_id": child_token_id
        })

    @external
    def detach_external_child(
        self,
        caller: Address,
        parent_id: U64,
        child_contract: Address,
        child_token_id: U64,
        recipient: Address
    ):
        """
        Detach external child NFT and transfer it to the recipient address.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_exists(parent_id)

        root_owner = self._get_root_owner(parent_id)
        if caller != root_owner:
            raise ContractError.UNAUTHORIZED

        key_prefix = f"ext_{parent_id}_{child_contract}_{child_token_id}"
        if not self.storage.get(f"{key_prefix}_owned", False):
            raise ContractError.CHILD_NOT_FOUND

        self.storage.remove(f"{key_prefix}_owned")

        # Transfer external NFT out of escrow
        self.env.call(child_contract, "transfer", self.env.current_contract_address(), recipient, child_token_id)

        self.env.emit_event("external_child_detached", {
            "parent_id": parent_id,
            "child_contract": child_contract,
            "child_token_id": child_token_id,
            "recipient": recipient
        })

    # --- BATCH COMPOSITION ---

    @external
    def attach_child_tokens_batch(self, caller: Address, parent_id: U64, child_ids: Vec):
        """Attach multiple internal child tokens to a parent token."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()
        self._require_exists(parent_id)

        root_parent_owner = self._get_root_owner(parent_id)
        if caller != root_parent_owner:
            raise ContractError.UNAUTHORIZED

        for i in range(len(child_ids)):
            child_id = child_ids.get(i)
            self._require_exists(child_id)
            
            if parent_id == child_id:
                raise ContractError.LOOP_DETECTED

            root_child_owner = self._get_root_owner(child_id)
            if caller != root_child_owner:
                raise ContractError.UNAUTHORIZED

            self._detect_loop_and_depth(child_id, parent_id)
            self.storage.set(f"parent_{child_id}", parent_id)
            self.storage.remove(f"owner_address_{child_id}")

            self.env.emit_event("child_token_attached", {
                "parent_id": parent_id,
                "child_id": child_id,
                "operator": caller
            })

    # --- VIEWS ---

    @view
    def get_parent(self, token_id: U64) -> Map:
        """Returns owner details (either parent token or direct address)."""
        self._require_initialized()
        self._require_exists(token_id)

        res = Map(self.env)
        parent = self.storage.get(f"parent_{token_id}", U64(0))
        if parent > U64(0):
            res.set("parent_type", Symbol("token"))
            res.set("parent_id", parent)
        else:
            res.set("parent_type", Symbol("address"))
            res.set("address", self.storage.get(f"owner_address_{token_id}"))
        return res

    @view
    def get_root_owner(self, token_id: U64) -> Address:
        """Finds the root address owning the composite structure."""
        self._require_initialized()
        self._require_exists(token_id)
        return self._get_root_owner(token_id)

    @view
    def is_external_child_attached(self, parent_id: U64, child_contract: Address, child_token_id: U64) -> Bool:
        """Checks if external child is attached to a parent token."""
        key_prefix = f"ext_{parent_id}_{child_contract}_{child_token_id}"
        return self.storage.get(f"{key_prefix}_owned", False)

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

    def _require_exists(self, token_id: U64):
        # Must have owner_address or parent > 0
        parent = self.storage.get(f"parent_{token_id}")
        owner_addr = self.storage.get(f"owner_address_{token_id}")
        if parent is None and owner_addr is None:
            raise ContractError.TOKEN_NOT_FOUND

    def _get_root_owner(self, token_id: U64) -> Address:
        """Recursively checks parentage up to root address."""
        current = token_id
        depth = 0
        while True:
            parent = self.storage.get(f"parent_{current}", U64(0))
            if parent == U64(0):
                return self.storage.get(f"owner_address_{current}")
            current = parent
            depth += 1
            if depth > 4:
                raise ContractError.MAX_DEPTH_EXCEEDED

    def _detect_loop_and_depth(self, child_id: U64, proposed_parent_id: U64):
        """Ensure attaching proposed_parent_id does not loop or violate depth limits."""
        current = proposed_parent_id
        depth = 1  # Attaching starts at depth 1 (child directly under proposed_parent)

        while current != U64(0):
            if current == child_id:
                raise ContractError.LOOP_DETECTED
            
            current = self.storage.get(f"parent_{current}", U64(0))
            depth += 1
            if depth > 4:  # Hard depth limit of 4 nested layers
                raise ContractError.MAX_DEPTH_EXCEEDED
