"""
Liquidity Pool Bridge — Swap verification, instant transfer, and LP fee shares.

Mycelium Smart Contract for Stellar. Enables rapid cross-chain transfers via
liquidity pools. Liquidity providers deposit assets to earn fee shares.
Swaps from foreign chains are verified via validator threshold signatures and 
settled instantly from the local pool.
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
    INSUFFICIENT_LIQUIDITY = 5
    INVALID_SHARE_AMOUNT = 6
    REPLAYED_SWAP = 7
    INVALID_SIGNATURE = 8
    THRESHOLD_NOT_MET = 9
    ZERO_DEPOSIT = 10
    INVALID_VALIDATORS = 11

@contract
class LiquidityBridge:
    """
    Bridge using local liquidity pools for instant cross-chain settlements.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        token: Address,
        validators: Vec, # Vec of Bytes (validator public keys)
        threshold: U64
    ):
        """Initialize contract state, token address, and validators registry."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if len(validators) == 0 or threshold == U64(0) or threshold > len(validators):
            raise ContractError.INVALID_VALIDATORS

        self.storage.set("admin", admin)
        self.storage.set("token", token)
        self.storage.set("total_shares", U128(0))
        self.storage.set("paused", False)

        # Set validators
        self.storage.set("threshold", threshold)
        self.storage.set("validator_count", len(validators))
        for i in range(len(validators)):
            pubkey = validators.get(i)
            self.storage.set(f"validator_{i}", pubkey)
            self.storage.set(f"is_validator_{pubkey}", True)

        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "token": token,
            "threshold": threshold
        })

    @external
    def add_liquidity(self, caller: Address, amount: U128) -> U128:
        """
        Deposit tokens into the pool. Mints LP shares to the depositor.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if amount == U128(0):
            raise ContractError.ZERO_DEPOSIT

        token = self.storage.get("token")
        contract_addr = self.env.current_contract_address()

        # Get pool balance BEFORE transferring new tokens
        pool_balance = self._get_pool_balance()
        total_shares = self.storage.get("total_shares", U128(0))

        # Transfer tokens to contract
        self.env.call(token, "transfer", caller, contract_addr, amount)

        # Calculate shares to mint
        if total_shares == U128(0) or pool_balance == U128(0):
            shares_to_mint = amount
        else:
            shares_to_mint = (amount * total_shares) / pool_balance

        # Update shares in storage
        self.storage.set("total_shares", total_shares + shares_to_mint)
        user_shares = self.storage.get(f"shares_{caller}", U128(0))
        self.storage.set(f"shares_{caller}", user_shares + shares_to_mint)

        self.env.emit_event("liquidity_added", {
            "lp": caller,
            "amount": amount,
            "shares_minted": shares_to_mint,
            "total_shares": total_shares + shares_to_mint
        })

        return shares_to_mint

    @external
    def remove_liquidity(self, caller: Address, shares: U128) -> U128:
        """
        Burn LP shares to redeem tokens + fee earnings.
        """
        caller.require_auth()
        self._require_initialized()

        user_shares = self.storage.get(f"shares_{caller}", U128(0))
        if shares == U128(0) or shares > user_shares:
            raise ContractError.INVALID_SHARE_AMOUNT

        total_shares = self.storage.get("total_shares", U128(0))
        pool_balance = self._get_pool_balance()

        # Calculate amount to redeem
        redeem_amount = (shares * pool_balance) / total_shares

        # Update shares in storage
        self.storage.set("total_shares", total_shares - shares)
        self.storage.set(f"shares_{caller}", user_shares - shares)

        # Transfer tokens back to LP
        token = self.storage.get("token")
        contract_addr = self.env.current_contract_address()
        self.env.call(token, "transfer", contract_addr, caller, redeem_amount)

        self.env.emit_event("liquidity_removed", {
            "lp": caller,
            "shares_burned": shares,
            "redeemed_amount": redeem_amount,
            "total_shares": total_shares - shares
        })

        return redeem_amount

    @external
    def swap_and_release(
        self,
        recipient: Address,
        amount: U128,
        lp_fee: U128,
        source_tx_hash: Bytes,
        signatures: Vec
    ):
        """
        Execute instant transfer to recipient from local pool.
        Verifies signatures of cross-chain event validators.
        The `lp_fee` stays in the pool, increasing LP share value.
        """
        self._require_initialized()
        self._require_not_paused()

        # Prevent double-spending
        if self.storage.get(f"processed_swap_{source_tx_hash}", False):
            raise ContractError.REPLAYED_SWAP

        # Validate we have enough liquidity in pool
        pool_balance = self._get_pool_balance()
        net_amount = amount - lp_fee
        if net_amount > pool_balance:
            raise ContractError.INSUFFICIENT_LIQUIDITY

        # Verify validator signatures
        message = self._construct_message(recipient, amount, lp_fee, source_tx_hash)
        threshold = self.storage.get("threshold", U64(0))
        valid_count = U64(0)
        
        used_validators = Map(self.env)
        val_count = self.storage.get("validator_count", U64(0))

        for i in range(len(signatures)):
            sig = signatures.get(i)
            matched = False

            for j in range(int(val_count)):
                val_pubkey = self.storage.get(f"validator_{j}")
                if used_validators.get(val_pubkey, False):
                    continue

                if self.env.crypto().verify_sig_ed25519(val_pubkey, message, sig):
                    used_validators.set(val_pubkey, True)
                    valid_count += U64(1)
                    matched = True
                    break

            if not matched:
                raise ContractError.INVALID_SIGNATURE

        if valid_count < threshold:
            raise ContractError.THRESHOLD_NOT_MET

        # Mark swap as processed
        self.storage.set(f"processed_swap_{source_tx_hash}", True)

        # Release net amount to recipient from contract
        token = self.storage.get("token")
        contract_addr = self.env.current_contract_address()
        self.env.call(token, "transfer", contract_addr, recipient, net_amount)

        # Note: the lp_fee remains in the contract, naturally boosting the share price
        # because the pool balance has only decreased by (amount - lp_fee), while the
        # source chain would lock the full `amount`.

        self.env.emit_event("swap_released", {
            "recipient": recipient,
            "amount": amount,
            "lp_fee": lp_fee,
            "net_amount": net_amount,
            "source_tx_hash": source_tx_hash
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause pool interactions (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_pool_info(self) -> Map:
        """Query pooled token balance and shares status."""
        self._require_initialized()
        res = Map(self.env)
        res.set("pool_balance", self._get_pool_balance())
        res.set("total_shares", self.storage.get("total_shares", U128(0)))
        return res

    @view
    def get_lp_shares(self, lp: Address) -> U128:
        """Query specific LP shares balance."""
        return self.storage.get(f"shares_{lp}", U128(0))

    @view
    def is_swap_processed(self, source_tx_hash: Bytes) -> Bool:
        """Check if a cross-chain swap has been completed locally."""
        return self.storage.get(f"processed_swap_{source_tx_hash}", False)

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

    def _get_pool_balance(self) -> U128:
        """Return the balance of bridged token held by the contract."""
        token = self.storage.get("token")
        # In Mycelium/Soroban, we query balance of the contract address
        contract_addr = self.env.current_contract_address()
        return self.env.call(token, "balance", contract_addr)

    def _construct_message(self, recipient: Address, amount: U128, lp_fee: U128, tx_hash: Bytes) -> Bytes:
        """Formulate message bytes to verify validator signatures."""
        payload = Bytes(self.env)
        payload.concat(tx_hash)
        payload.concat(Bytes(self.env, str(recipient).encode("utf-8")))
        payload.concat(Bytes(self.env, str(amount).encode("utf-8")))
        payload.concat(Bytes(self.env, str(lp_fee).encode("utf-8")))
        return payload
