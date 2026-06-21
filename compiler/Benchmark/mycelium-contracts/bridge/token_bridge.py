"""
Token Bridge — Lock-and-mint bridge.

Mycelium Smart Contract for Stellar. Locks native/Stellar tokens locally
and emits lock events. Releases locked tokens upon receiving attestation
signatures from a threshold of registered validators. Implements daily limits
and fee shares for the bridge operator.
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
    INVALID_LIMIT = 5
    LIMIT_EXCEEDED = 6
    INVALID_SIGNATURE = 7
    DUPLICATE_SIGNATURE = 8
    THRESHOLD_NOT_MET = 9
    REPLAYED_TX = 10
    INVALID_BPS = 11
    INVALID_VALIDATOR_COUNT = 12

@contract
class TokenBridge:
    """
    Lock-and-mint bridge contract verifying validator signatures to release assets.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        token: Address,
        validators: Vec, # Vec of Bytes (public keys of validators)
        threshold: U64,
        daily_limit: U128,
        fee_recipient: Address,
        fee_bps: U64
    ):
        """Initialize bridge parameters, validators list, and limits."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if len(validators) == 0 or threshold == U64(0) or threshold > len(validators):
            raise ContractError.INVALID_VALIDATOR_COUNT

        if fee_bps > U64(10000):
            raise ContractError.INVALID_BPS

        self.storage.set("admin", admin)
        self.storage.set("token", token)
        self.storage.set("threshold", threshold)
        self.storage.set("daily_limit", daily_limit)
        self.storage.set("fee_recipient", fee_recipient)
        self.storage.set("fee_bps", fee_bps)
        self.storage.set("daily_spent", U128(0))
        self.storage.set("last_reset_time", self._get_now())
        self.storage.set("lock_nonce", U64(1))
        self.storage.set("paused", False)
        
        # Store validators
        self.storage.set("validator_count", len(validators))
        for i in range(len(validators)):
            pubkey = validators.get(i)
            self.storage.set(f"validator_{i}", pubkey)
            self.storage.set(f"is_validator_{pubkey}", True)

        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "token": token,
            "threshold": threshold,
            "daily_limit": daily_limit
        })

    @external
    def lock_tokens(self, caller: Address, amount: U128, foreign_recipient: Bytes):
        """
        Lock tokens on this chain to be minted/released on the destination chain.
        Applies transfer fees and daily limits.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        # Check and update daily limit
        self._check_limit(amount)

        # Calculate fees
        fee_bps = self.storage.get("fee_bps", U64(0))
        fee = (amount * U128(fee_bps)) / U128(10000)
        net_amount = amount - fee

        token = self.storage.get("token")
        contract_addr = self.env.current_contract_address()

        # Transfer tokens from caller to contract
        if amount > U128(0):
            self.env.call(token, "transfer", caller, contract_addr, amount)

            # Distribute fee to fee recipient
            if fee > U128(0):
                fee_recipient = self.storage.get("fee_recipient")
                self.env.call(token, "transfer", contract_addr, fee_recipient, fee)

        nonce = self.storage.get("lock_nonce", U64(1))
        self.storage.set("lock_nonce", nonce + U64(1))

        self.env.emit_event("tokens_locked", {
            "sender": caller,
            "net_amount": net_amount,
            "fee": fee,
            "foreign_recipient": foreign_recipient,
            "nonce": nonce
        })

    @external
    def release_tokens(
        self,
        recipient: Address,
        amount: U128,
        tx_hash: Bytes, # Foreign transaction hash to prevent double claim
        signatures: Vec # Vec of Bytes (signatures)
    ):
        """
        Release tokens on this chain using a threshold of validator signatures.
        """
        self._require_initialized()
        self._require_not_paused()

        # Prevent transaction replays
        if self.storage.get(f"processed_tx_{tx_hash}", False):
            raise ContractError.REPLAYED_TX

        # Re-construct message that was signed
        # Message format is hash(recipient + amount + tx_hash)
        message = self._construct_message(recipient, amount, tx_hash)

        # Verify validator signatures
        threshold = self.storage.get("threshold", U64(0))
        valid_sigs_count = U64(0)
        
        # Keep track of checked validators to prevent duplicate signatures from same validator
        used_validators = Map(self.env)

        for i in range(len(signatures)):
            sig = signatures.get(i)
            # Find which validator public key matches this signature
            val_count = self.storage.get("validator_count", U64(0))
            matched = False

            for j in range(int(val_count)):
                val_pubkey = self.storage.get(f"validator_{j}")
                if used_validators.get(val_pubkey, False):
                    continue

                # Verify signature using ed25519
                if self.env.crypto().verify_sig_ed25519(val_pubkey, message, sig):
                    used_validators.set(val_pubkey, True)
                    valid_sigs_count += U64(1)
                    matched = True
                    break

            if not matched:
                raise ContractError.INVALID_SIGNATURE

        if valid_sigs_count < threshold:
            raise ContractError.THRESHOLD_NOT_MET

        # Mark transaction as processed
        self.storage.set(f"processed_tx_{tx_hash}", True)

        # Transfer tokens to recipient
        token = self.storage.get("token")
        contract_addr = self.env.current_contract_address()
        self.env.call(token, "transfer", contract_addr, recipient, amount)

        self.env.emit_event("tokens_released", {
            "recipient": recipient,
            "amount": amount,
            "tx_hash": tx_hash
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause the bridge contract (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("bridge_paused", {"paused": paused})

    @external
    def update_limits(self, caller: Address, new_limit: U128, new_fee_bps: U64):
        """Update daily transfer limit and bridge fee (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        if new_fee_bps > U64(10000):
            raise ContractError.INVALID_BPS

        self.storage.set("daily_limit", new_limit)
        self.storage.set("fee_bps", new_fee_bps)

        self.env.emit_event("limits_updated", {
            "new_limit": new_limit,
            "new_fee_bps": new_fee_bps
        })

    @external
    def update_validators(self, caller: Address, new_validators: Vec, new_threshold: U64):
        """Update the validator registry and signature threshold (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        if len(new_validators) == 0 or new_threshold == U64(0) or new_threshold > len(new_validators):
            raise ContractError.INVALID_VALIDATOR_COUNT

        # Clean old validators
        old_count = self.storage.get("validator_count", U64(0))
        for i in range(int(old_count)):
            old_pubkey = self.storage.get(f"validator_{i}")
            self.storage.remove(f"is_validator_{old_pubkey}")
            self.storage.remove(f"validator_{i}")

        # Set new validators
        self.storage.set("validator_count", len(new_validators))
        for i in range(len(new_validators)):
            pubkey = new_validators.get(i)
            self.storage.set(f"validator_{i}", pubkey)
            self.storage.set(f"is_validator_{pubkey}", True)

        self.storage.set("threshold", new_threshold)

        self.env.emit_event("validators_updated", {
            "validator_count": len(new_validators),
            "threshold": new_threshold
        })

    # --- VIEWS ---

    @view
    def get_daily_status(self) -> Map:
        """Get information on daily limits and remaining quota."""
        self._require_initialized()
        res = Map(self.env)
        limit = self.storage.get("daily_limit", U128(0))
        
        now = self._get_now()
        last_reset = self.storage.get("last_reset_time", U64(0))
        spent = self.storage.get("daily_spent", U128(0))
        
        if now >= last_reset + U64(86400):
            spent = U128(0)

        res.set("limit", limit)
        res.set("spent", spent)
        res.set("remaining", limit - spent)
        res.set("next_reset", last_reset + U64(86400))
        return res

    @view
    def get_validators(self) -> Vec:
        """Get the list of registered validator public keys."""
        self._require_initialized()
        res = Vec(self.env)
        count = self.storage.get("validator_count", U64(0))
        for i in range(int(count)):
            res.push_back(self.storage.get(f"validator_{i}"))
        return res

    @view
    def is_tx_processed(self, tx_hash: Bytes) -> Bool:
        """Check if a cross-chain tx has already been released."""
        return self.storage.get(f"processed_tx_{tx_hash}", False)

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

    def _get_now(self) -> U64:
        return self.env.ledger_timestamp()

    def _check_limit(self, amount: U128):
        limit = self.storage.get("daily_limit", U128(0))
        now = self._get_now()
        last_reset = self.storage.get("last_reset_time", U64(0))
        spent = self.storage.get("daily_spent", U128(0))

        if now >= last_reset + U64(86400):
            spent = U128(0)
            self.storage.set("last_reset_time", now)

        if spent + amount > limit:
            raise ContractError.LIMIT_EXCEEDED

        self.storage.set("daily_spent", spent + amount)

    def _construct_message(self, recipient: Address, amount: U128, tx_hash: Bytes) -> Bytes:
        """
        Concatenate message parameters to form the unique payload signed by validators.
        """
        # We can build a unique message payload byte string
        # Let's concatenate them as Bytes
        # In a real environment, we'd hash the binary representation.
        # We represent the message payload directly by creating a Bytes object
        # which starts with the tx_hash and appends stringified/encoded representations.
        # Let's create a dynamic bytes payload
        payload = Bytes(self.env)
        payload.concat(tx_hash)
        payload.concat(Bytes(self.env, str(recipient).encode("utf-8")))
        payload.concat(Bytes(self.env, str(amount).encode("utf-8")))
        return payload
