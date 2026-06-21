"""
Atomic Swap HTLC — Hash Time-Locked Contract.

Mycelium Smart Contract for Stellar. Enables trustless cross-chain or on-chain
swaps. Tokens are locked in the contract with a hash lock (sha256 preimage)
and a timelock. The receiver claims by revealing the preimage.
If the timelock expires without claim, the sender can refund the tokens.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)

class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    SWAP_ALREADY_EXISTS = 4
    SWAP_NOT_FOUND = 5
    SWAP_NOT_ACTIVE = 6
    INVALID_TIMELOCK = 7
    INVALID_PREIMAGE = 8
    TIMELOCK_NOT_EXPIRED = 9
    TIMELOCK_EXPIRED = 10
    INVALID_AMOUNT = 11
    PAUSED = 12

# Swap Status Enum
# 0 = UNINITIALIZED, 1 = ACTIVE, 2 = CLAIMED, 3 = REFUNDED
STATUS_UNINITIALIZED = U64(0)
STATUS_ACTIVE = U64(1)
STATUS_CLAIMED = U64(2)
STATUS_REFUNDED = U64(3)

@contract
class AtomicSwap:
    """
    Hashed Time-Locked Contract (HTLC) for atomic swap operations.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """Initialize contract admin and state."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin})

    @external
    def lock_tokens(
        self,
        caller: Address,
        swap_id: Bytes,
        receiver: Address,
        hash_lock: Bytes,
        timelock: U64,
        amount: U128,
        token: Address
    ):
        """
        Lock tokens in the contract.
        - swap_id: Unique identifier for the swap
        - receiver: Intended recipient of the locked tokens
        - hash_lock: sha256 hash of the secret preimage
        - timelock: Unix timestamp after which sender can claim refund
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        # Ensure swap_id is not already in use
        status = self.storage.get(f"status_{swap_id}", STATUS_UNINITIALIZED)
        if status != STATUS_UNINITIALIZED:
            raise ContractError.SWAP_ALREADY_EXISTS

        # Validate amount
        if amount == U128(0):
            raise ContractError.INVALID_AMOUNT

        # Validate timelock (must be in the future, e.g. at least 15 minutes from now)
        now = self._get_now()
        if timelock <= now + U64(900): # 900 seconds = 15 minutes minimum timelock buffer
            raise ContractError.INVALID_TIMELOCK

        # Transfer tokens from caller to contract
        contract_addr = self.env.current_contract_address()
        self.env.call(token, "transfer", caller, contract_addr, amount)

        # Store swap details
        self.storage.set(f"sender_{swap_id}", caller)
        self.storage.set(f"receiver_{swap_id}", receiver)
        self.storage.set(f"amount_{swap_id}", amount)
        self.storage.set(f"token_{swap_id}", token)
        self.storage.set(f"hash_lock_{swap_id}", hash_lock)
        self.storage.set(f"timelock_{swap_id}", timelock)
        self.storage.set(f"status_{swap_id}", STATUS_ACTIVE)

        self.env.emit_event("tokens_locked", {
            "swap_id": swap_id,
            "sender": caller,
            "receiver": receiver,
            "amount": amount,
            "token": token,
            "hash_lock": hash_lock,
            "timelock": timelock
        })

    @external
    def claim_tokens(self, swap_id: Bytes, preimage: Bytes):
        """
        Claim tokens by providing the correct preimage before the timelock.
        """
        self._require_initialized()
        self._require_not_paused()

        # Check swap exists and is active
        status = self.storage.get(f"status_{swap_id}", STATUS_UNINITIALIZED)
        if status == STATUS_UNINITIALIZED:
            raise ContractError.SWAP_NOT_FOUND
        if status != STATUS_ACTIVE:
            raise ContractError.SWAP_NOT_ACTIVE

        # Check timelock (cannot claim after timelock expiration)
        timelock = self.storage.get(f"timelock_{swap_id}", U64(0))
        if self._get_now() >= timelock:
            raise ContractError.TIMELOCK_EXPIRED

        # Verify hash lock
        hash_lock = self.storage.get(f"hash_lock_{swap_id}")
        computed_hash = self.env.crypto().sha256(preimage)
        if computed_hash != hash_lock:
            raise ContractError.INVALID_PREIMAGE

        # Update swap status to CLAIMED
        self.storage.set(f"status_{swap_id}", STATUS_CLAIMED)

        # Transfer tokens to the receiver
        receiver = self.storage.get(f"receiver_{swap_id}")
        amount = self.storage.get(f"amount_{swap_id}", U128(0))
        token = self.storage.get(f"token_{swap_id}")
        contract_addr = self.env.current_contract_address()

        self.env.call(token, "transfer", contract_addr, receiver, amount)

        self.env.emit_event("tokens_claimed", {
            "swap_id": swap_id,
            "receiver": receiver,
            "preimage": preimage,
            "amount": amount
        })

    @external
    def refund_tokens(self, swap_id: Bytes):
        """
        Refund tokens to the sender if the timelock has expired.
        """
        self._require_initialized()

        # Check swap exists and is active
        status = self.storage.get(f"status_{swap_id}", STATUS_UNINITIALIZED)
        if status == STATUS_UNINITIALIZED:
            raise ContractError.SWAP_NOT_FOUND
        if status != STATUS_ACTIVE:
            raise ContractError.SWAP_NOT_ACTIVE

        # Check timelock (refund is only allowed after timelock)
        timelock = self.storage.get(f"timelock_{swap_id}", U64(0))
        if self._get_now() < timelock:
            raise ContractError.TIMELOCK_NOT_EXPIRED

        # Update swap status to REFUNDED
        self.storage.set(f"status_{swap_id}", STATUS_REFUNDED)

        # Transfer tokens back to sender
        sender = self.storage.get(f"sender_{swap_id}")
        amount = self.storage.get(f"amount_{swap_id}", U128(0))
        token = self.storage.get(f"token_{swap_id}")
        contract_addr = self.env.current_contract_address()

        self.env.call(token, "transfer", contract_addr, sender, amount)

        self.env.emit_event("tokens_refunded", {
            "swap_id": swap_id,
            "sender": sender,
            "amount": amount
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause or unpause lock/claim operations (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_swap_details(self, swap_id: Bytes) -> Map:
        """Inspect detailed parameters and state of a swap."""
        res = Map(self.env)
        status = self.storage.get(f"status_{swap_id}", STATUS_UNINITIALIZED)
        if status != STATUS_UNINITIALIZED:
            res.set("sender", self.storage.get(f"sender_{swap_id}"))
            res.set("receiver", self.storage.get(f"receiver_{swap_id}"))
            res.set("amount", self.storage.get(f"amount_{swap_id}"))
            res.set("token", self.storage.get(f"token_{swap_id}"))
            res.set("hash_lock", self.storage.get(f"hash_lock_{swap_id}"))
            res.set("timelock", self.storage.get(f"timelock_{swap_id}"))
            res.set("status", status)
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

    def _get_now(self) -> U64:
        return self.env.ledger_timestamp()
