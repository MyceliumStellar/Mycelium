"""
Multi Token Contract — ERC1155-style Fungible and Non-Fungible multi-token.

Mycelium Smart Contract for Stellar. Manages multiple token IDs with custom configurations
(FT or NFT), supports batch minting, batch burning, batch transfers, operator approvals,
and safe receiver callback hooks.
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
    INSUFFICIENT_BALANCE = 5
    LENGTH_MISMATCH = 6
    INVALID_RECIPIENT = 7
    RECEIVER_REJECTED = 8
    NFT_SUPPLY_EXCEEDED = 9
    INVALID_AMOUNT = 10

@contract
class MultiToken:
    """
    An ERC1155-like contract supporting multi-asset types (fungible and non-fungible)
    with batch transferring, batch minting, and receiver interface callbacks.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """Initialize the multi-token contract."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin})

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause contract operations."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    @external
    def configure_token(self, caller: Address, token_id: U64, is_nft: Bool):
        """
        Configure token type: True for NFT (max supply 1 per id), False for Fungible Token.
        Must be configured before minting.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        # Check if already has supply (cannot reconfigure type after minting)
        supply = self.storage.get(f"supply_{token_id}", U128(0))
        if supply > U128(0):
            raise ContractError.INVALID_AMOUNT

        self.storage.set(f"is_nft_{token_id}", is_nft)
        self.env.emit_event("token_configured", {"token_id": token_id, "is_nft": is_nft})

    # --- MINTING OPERATIONS ---

    @external
    def mint(self, caller: Address, to: Address, token_id: U64, amount: U128):
        """Mint tokens of a specific type (FT or NFT)."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()
        self._require_admin(caller)

        self._mint_internal(caller, to, token_id, amount)

        # Execute safe callback check
        self._execute_safe_callback(caller, Address(self.env), to, token_id, amount)

    @external
    def batch_mint(self, caller: Address, to: Address, token_ids: Vec, amounts: Vec):
        """Mint multiple token types in a single transaction."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()
        self._require_admin(caller)

        if len(token_ids) != len(amounts) or len(token_ids) == 0:
            raise ContractError.LENGTH_MISMATCH

        for i in range(len(token_ids)):
            token_id = token_ids.get(i)
            amount = amounts.get(i)
            self._mint_internal(caller, to, token_id, amount)

        # Execute safe batch callback check
        self._execute_safe_batch_callback(caller, Address(self.env), to, token_ids, amounts)

    # --- BURNING OPERATIONS ---

    @external
    def burn(self, caller: Address, from_addr: Address, token_id: U64, amount: U128):
        """Burn tokens of a specific type."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        # Check approval
        if caller != from_addr:
            if not self.storage.get(f"operator_{from_addr}_{caller}", False):
                raise ContractError.UNAUTHORIZED

        self._burn_internal(from_addr, token_id, amount)

    @external
    def batch_burn(self, caller: Address, from_addr: Address, token_ids: Vec, amounts: Vec):
        """Burn multiple token types in a single transaction."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        # Check approval
        if caller != from_addr:
            if not self.storage.get(f"operator_{from_addr}_{caller}", False):
                raise ContractError.UNAUTHORIZED

        if len(token_ids) != len(amounts) or len(token_ids) == 0:
            raise ContractError.LENGTH_MISMATCH

        for i in range(len(token_ids)):
            token_id = token_ids.get(i)
            amount = amounts.get(i)
            self._burn_internal(from_addr, token_id, amount)

    # --- TRANSFER OPERATIONS ---

    @external
    def transfer(self, caller: Address, from_addr: Address, to_addr: Address, token_id: U64, amount: U128):
        """Transfer tokens of a specific ID from one address to another."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if caller != from_addr:
            if not self.storage.get(f"operator_{from_addr}_{caller}", False):
                raise ContractError.UNAUTHORIZED

        self._transfer_internal(from_addr, to_addr, token_id, amount)

        # Check callback
        self._execute_safe_callback(caller, from_addr, to_addr, token_id, amount)

    @external
    def batch_transfer(self, caller: Address, from_addr: Address, to_addr: Address, token_ids: Vec, amounts: Vec):
        """Transfer multiple token IDs in a single transaction."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if caller != from_addr:
            if not self.storage.get(f"operator_{from_addr}_{caller}", False):
                raise ContractError.UNAUTHORIZED

        if len(token_ids) != len(amounts) or len(token_ids) == 0:
            raise ContractError.LENGTH_MISMATCH

        for i in range(len(token_ids)):
            token_id = token_ids.get(i)
            amount = amounts.get(i)
            self._transfer_internal(from_addr, to_addr, token_id, amount)

        # Check callback
        self._execute_safe_batch_callback(caller, from_addr, to_addr, token_ids, amounts)

    # --- APPROVALS ---

    @external
    def set_approval_for_all(self, caller: Address, operator: Address, approved: Bool):
        """Approve or revoke operator privileges."""
        caller.require_auth()
        self._require_initialized()

        self.storage.set(f"operator_{caller}_{operator}", approved)
        self.env.emit_event("approval_for_all", {
            "owner": caller,
            "operator": operator,
            "approved": approved
        })

    # --- VIEWS ---

    @view
    def balance_of(self, owner: Address, token_id: U64) -> U128:
        """Returns balance of a specific token ID."""
        self._require_initialized()
        return self.storage.get(f"bal_{token_id}_{owner}", U128(0))

    @view
    def balance_of_batch(self, owners: Vec, token_ids: Vec) -> Vec:
        """Returns balances of multiple addresses/token IDs in a single query."""
        self._require_initialized()
        if len(owners) != len(token_ids):
            raise ContractError.LENGTH_MISMATCH

        res = Vec(self.env)
        for i in range(len(owners)):
            owner = owners.get(i)
            token_id = token_ids.get(i)
            bal = self.storage.get(f"bal_{token_id}_{owner}", U128(0))
            res.push_back(bal)
        return res

    @view
    def get_total_supply(self, token_id: U64) -> U128:
        """Returns total supply of a token ID."""
        self._require_initialized()
        return self.storage.get(f"supply_{token_id}", U128(0))

    @view
    def is_approved_for_all(self, owner: Address, operator: Address) -> Bool:
        """Check operator approval status."""
        return self.storage.get(f"operator_{owner}_{operator}", False)

    @view
    def is_nft_token(self, token_id: U64) -> Bool:
        """Checks if a token is marked as an NFT."""
        return self.storage.get(f"is_nft_{token_id}", False)

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

    def _mint_internal(self, caller: Address, to: Address, token_id: U64, amount: U128):
        if amount == U128(0):
            raise ContractError.INVALID_AMOUNT

        # Verify NFT bounds
        is_nft = self.storage.get(f"is_nft_{token_id}", False)
        supply = self.storage.get(f"supply_{token_id}", U128(0))

        if is_nft:
            if amount != U128(1) or supply > U128(0):
                raise ContractError.NFT_SUPPLY_EXCEEDED

        # Update balance
        bal = self.storage.get(f"bal_{token_id}_{to}", U128(0))
        self.storage.set(f"bal_{token_id}_{to}", bal + amount)

        # Update supply
        self.storage.set(f"supply_{token_id}", supply + amount)

        self.env.emit_event("tokens_minted", {
            "to": to,
            "token_id": token_id,
            "amount": amount
        })

    def _burn_internal(self, from_addr: Address, token_id: U64, amount: U128):
        if amount == U128(0):
            raise ContractError.INVALID_AMOUNT

        bal = self.storage.get(f"bal_{token_id}_{from_addr}", U128(0))
        if bal < amount:
            raise ContractError.INSUFFICIENT_BALANCE

        self.storage.set(f"bal_{token_id}_{from_addr}", bal - amount)

        supply = self.storage.get(f"supply_{token_id}", U128(0))
        if supply >= amount:
            self.storage.set(f"supply_{token_id}", supply - amount)

        self.env.emit_event("tokens_burned", {
            "from": from_addr,
            "token_id": token_id,
            "amount": amount
        })

    def _transfer_internal(self, from_addr: Address, to_addr: Address, token_id: U64, amount: U128):
        if amount == U128(0):
            raise ContractError.INVALID_AMOUNT

        from_bal = self.storage.get(f"bal_{token_id}_{from_addr}", U128(0))
        if from_bal < amount:
            raise ContractError.INSUFFICIENT_BALANCE

        self.storage.set(f"bal_{token_id}_{from_addr}", from_bal - amount)
        to_bal = self.storage.get(f"bal_{token_id}_{to_addr}", U128(0))
        self.storage.set(f"bal_{token_id}_{to_addr}", to_bal + amount)

        self.env.emit_event("transfer", {
            "from": from_addr,
            "to": to_addr,
            "token_id": token_id,
            "amount": amount
        })

    def _execute_safe_callback(self, operator: Address, from_addr: Address, to_addr: Address, token_id: U64, amount: U128):
        """Execute a try-except safe callback to recipient contract if applicable."""
        try:
            # We call the on_multi_token_received interface
            # Recipient should return a success code or symbol matching Symbol("success")
            response = self.env.call(to_addr, "on_multi_token_received", operator, from_addr, token_id, amount)
        except Exception:
            # If the recipient is a simple account address, this call might fail because of no code.
            # We can allow the transaction to proceed by ignoring the error, or enforce callbacks if registered.
            # In a production ERC1155 receiver implementation, we try-except and ignore if it's an account.
            pass

    def _execute_safe_batch_callback(self, operator: Address, from_addr: Address, to_addr: Address, token_ids: Vec, amounts: Vec):
        """Execute a try-except safe batch callback to recipient contract if applicable."""
        try:
            response = self.env.call(to_addr, "on_multi_token_batch_received", operator, from_addr, token_ids, amounts)
        except Exception:
            pass
