"""
Message Bridge — Generalized cross-chain messaging, indexing, and replay protection.

Mycelium Smart Contract for Stellar. Allows sending and receiving arbitrary byte
payloads. Inbound messages verify validator signature thresholds before dispatching
callbacks to local receiver contracts. Replay protection is enforced by mapping chain-specific nonces.
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
    INVALID_VALIDATOR_COUNT = 5
    INVALID_SIGNATURE = 6
    THRESHOLD_NOT_MET = 7
    MESSAGE_ALREADY_PROCESSED = 8
    INSUFFICIENT_FEE = 9
    DISPATCH_FAILED = 10

@contract
class MessageBridge:
    """
    Generalized cross-chain message relayer with validation and execution.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        fee_token: Address,
        validators: Vec, # Vec of Bytes (pubkeys)
        threshold: U64
    ):
        """Initialize the message bridge with validators and fee parameters."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if len(validators) == 0 or threshold == U64(0) or threshold > len(validators):
            raise ContractError.INVALID_VALIDATOR_COUNT

        self.storage.set("admin", admin)
        self.storage.set("fee_token", fee_token)
        self.storage.set("outbound_nonce", U64(1))
        self.storage.set("paused", False)

        # Register validators
        self.storage.set("threshold", threshold)
        self.storage.set("validator_count", len(validators))
        for i in range(len(validators)):
            pubkey = validators.get(i)
            self.storage.set(f"validator_{i}", pubkey)
            self.storage.set(f"is_validator_{pubkey}", True)

        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "fee_token": fee_token,
            "threshold": threshold
        })

    @external
    def send_message(
        self,
        caller: Address,
        target_chain: Bytes,
        target_contract: Bytes,
        payload: Bytes
    ) -> U64:
        """
        Send an outbound message to a target contract on a foreign chain.
        Requires paying a cross-chain dispatch fee.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        # Collect fee
        fee_token = self.storage.get("fee_token")
        fee_amount = self.storage.get(f"fee_{target_chain}", U128(0))
        if fee_amount > U128(0):
            contract_addr = self.env.current_contract_address()
            self.env.call(fee_token, "transfer", caller, contract_addr, fee_amount)

        nonce = self.storage.get("outbound_nonce", U64(1))
        self.storage.set("outbound_nonce", nonce + U64(1))

        self.env.emit_event("message_sent", {
            "nonce": nonce,
            "sender": caller,
            "target_chain": target_chain,
            "target_contract": target_contract,
            "payload": payload,
            "fee": fee_amount
        })

        return nonce

    @external
    def execute_message(
        self,
        source_chain: Bytes,
        source_contract: Bytes,
        nonce: U64,
        target_contract: Address,
        payload: Bytes,
        signatures: Vec
    ):
        """
        Execute an inbound message verified by a threshold of validator signatures.
        Calls the destination contract's `handle_message` method.
        """
        self._require_initialized()
        self._require_not_paused()

        # Replay protection check
        if self.storage.get(f"inbound_{source_chain}_{nonce}", False):
            raise ContractError.MESSAGE_ALREADY_PROCESSED

        # Hash reconstruction: hash(source_chain + source_contract + nonce + target_contract + payload)
        message = self._construct_message(source_chain, source_contract, nonce, target_contract, payload)

        # Validate signatures
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

        # Mark message as processed before invoking callback to prevent re-entrancy
        self.storage.set(f"inbound_{source_chain}_{nonce}", True)

        # Dispatch call to the destination contract
        # Expected signature on target: handle_message(source_chain: Bytes, source_contract: Bytes, payload: Bytes)
        try:
            self.env.call(target_contract, "handle_message", source_chain, source_contract, payload)
        except Exception:
            # Revert state changes and raise error if the call fails
            raise ContractError.DISPATCH_FAILED

        self.env.emit_event("message_executed", {
            "source_chain": source_chain,
            "source_contract": source_contract,
            "nonce": nonce,
            "target": target_contract
        })

    @external
    def set_chain_fee(self, caller: Address, target_chain: Bytes, fee: U128):
        """Configure dispatch fee for a target chain (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        self.storage.set(f"fee_{target_chain}", fee)
        self.env.emit_event("fee_updated", {
            "target_chain": target_chain,
            "fee": fee
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause the message bridge (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_chain_fee(self, target_chain: Bytes) -> U128:
        """Get the dispatch fee for a target chain."""
        return self.storage.get(f"fee_{target_chain}", U128(0))

    @view
    def is_message_processed(self, source_chain: Bytes, nonce: U64) -> Bool:
        """Check if an inbound message has been executed."""
        return self.storage.get(f"inbound_{source_chain}_{nonce}", False)

    @view
    def get_outbound_nonce(self) -> U64:
        """Query the next outbound message nonce."""
        return self.storage.get("outbound_nonce", U64(1))

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

    def _construct_message(
        self,
        source_chain: Bytes,
        source_contract: Bytes,
        nonce: U64,
        target_contract: Address,
        payload: Bytes
    ) -> Bytes:
        """Construct the byte payload verifying the signature of validators."""
        msg = Bytes(self.env)
        msg.concat(source_chain)
        msg.concat(source_contract)
        msg.concat(Bytes(self.env, str(nonce).encode("utf-8")))
        msg.concat(Bytes(self.env, str(target_contract).encode("utf-8")))
        msg.concat(payload)
        return msg
