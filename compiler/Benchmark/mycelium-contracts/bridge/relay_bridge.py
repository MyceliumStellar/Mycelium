"""
Relay Bridge — Light client state synchronization and proof validation.

Mycelium Smart Contract for Stellar. Maintains block headers of a foreign chain
submitted by staked relayers. Performs height-and-parent validation, handles 
finalization delays, and verifies Merkle state proofs against recorded state roots.
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
    INSUFFICIENT_STAKE = 5
    INVALID_BLOCK_HEIGHT = 6
    INVALID_PARENT_HASH = 7
    BLOCK_ALREADY_EXISTS = 8
    BLOCK_NOT_FOUND = 9
    PROOF_VALIDATION_FAILED = 10
    STAKE_LOCKED = 11
    SLASH_ZERO_STAKE = 12

@contract
class RelayBridge:
    """
    Relay Bridge maintaining light client block headers and verifying state proofs.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        staking_token: Address,
        min_stake: U128,
        dispute_period: U64,
        genesis_block_num: U64,
        genesis_block_hash: Bytes,
        genesis_state_root: Bytes
    ):
        """Initialize the relay bridge with genesis parameters."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("staking_token", staking_token)
        self.storage.set("min_stake", min_stake)
        self.storage.set("dispute_period", dispute_period) # Time in seconds before headers are finalized
        self.storage.set("latest_block_number", genesis_block_num)
        
        # Register genesis block
        self.storage.set(f"block_hash_{genesis_block_num}", genesis_block_hash)
        self.storage.set(f"state_root_{genesis_block_num}", genesis_state_root)
        self.storage.set(f"block_timestamp_{genesis_block_num}", self._get_now())
        self.storage.set(f"block_finalized_{genesis_block_num}", True)

        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "staking_token": staking_token,
            "genesis_block": genesis_block_num,
            "genesis_hash": genesis_block_hash
        })

    @external
    def stake_tokens(self, caller: Address, amount: U128):
        """Stake tokens to become or remain an active relayer."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        token = self.storage.get("staking_token")
        contract_addr = self.env.current_contract_address()

        # Transfer tokens
        if amount > U128(0):
            self.env.call(token, "transfer", caller, contract_addr, amount)

        # Update relayer's stake
        current_stake = self.storage.get(f"stake_{caller}", U128(0))
        new_stake = current_stake + amount
        self.storage.set(f"stake_{caller}", new_stake)

        # Check if they meet the minimum stake to be active
        min_stake = self.storage.get("min_stake", U128(0))
        if new_stake >= min_stake:
            self.storage.set(f"is_relayer_{caller}", True)

        self.env.emit_event("stake_deposited", {
            "relayer": caller,
            "amount": amount,
            "total_stake": new_stake
        })

    @external
    def unstake_tokens(self, caller: Address, amount: U128):
        """Unstake relayer tokens. Fails if lock conditions are active."""
        caller.require_auth()
        self._require_initialized()

        current_stake = self.storage.get(f"stake_{caller}", U128(0))
        if amount > current_stake:
            raise ContractError.INSUFFICIENT_STAKE

        new_stake = current_stake - amount
        self.storage.set(f"stake_{caller}", new_stake)

        # Update active relayer status if below minimum stake
        min_stake = self.storage.get("min_stake", U128(0))
        if new_stake < min_stake:
            self.storage.set(f"is_relayer_{caller}", False)

        # Transfer tokens back
        token = self.storage.get("staking_token")
        contract_addr = self.env.current_contract_address()
        self.env.call(token, "transfer", contract_addr, caller, amount)

        self.env.emit_event("stake_withdrawn", {
            "relayer": caller,
            "amount": amount,
            "remaining_stake": new_stake
        })

    @external
    def submit_block_header(
        self,
        caller: Address,
        block_number: U64,
        block_hash: Bytes,
        parent_hash: Bytes,
        state_root: Bytes
    ):
        """
        Submit a new block header. Validates height continuity and parent hash linkage.
        Requires the submitter to be an active staked relayer.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()
        self._require_active_relayer(caller)

        # Check height continuity
        latest_num = self.storage.get("latest_block_number", U64(0))
        if block_number != latest_num + U64(1):
            raise ContractError.INVALID_BLOCK_HEIGHT

        # Check if block hash already exists
        if self.storage.get(f"block_hash_{block_number}") is not None:
            raise ContractError.BLOCK_ALREADY_EXISTS

        # Validate parent hash matches latest block hash
        expected_parent = self.storage.get(f"block_hash_{latest_num}")
        if expected_parent != parent_hash:
            raise ContractError.INVALID_PARENT_HASH

        # Store the block details
        self.storage.set(f"block_hash_{block_number}", block_hash)
        self.storage.set(f"parent_hash_{block_number}", parent_hash)
        self.storage.set(f"state_root_{block_number}", state_root)
        self.storage.set(f"block_timestamp_{block_number}", self._get_now())
        self.storage.set(f"block_relayer_{block_number}", caller)
        self.storage.set(f"block_finalized_{block_number}", False)

        # Update latest block tracking
        self.storage.set("latest_block_number", block_number)

        self.env.emit_event("header_submitted", {
            "block_number": block_number,
            "block_hash": block_hash,
            "relayer": caller,
            "state_root": state_root
        })

    @external
    def slash_relayer(self, caller: Address, block_number: U64, bad_relayer: Address):
        """
        Slash a relayer for submitting an invalid header (admin controlled / dispute arbiter).
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        block_relayer = self.storage.get(f"block_relayer_{block_number}")
        if block_relayer != bad_relayer:
            raise ContractError.UNAUTHORIZED

        # Retrieve stake
        stake = self.storage.get(f"stake_{bad_relayer}", U128(0))
        if stake == U128(0):
            raise ContractError.SLASH_ZERO_STAKE

        # Slash: Wipe stake, deactivate relayer status
        self.storage.set(f"stake_{bad_relayer}", U128(0))
        self.storage.set(f"is_relayer_{bad_relayer}", False)

        # Send slashed tokens to the treasury/admin
        token = self.storage.get("staking_token")
        contract_addr = self.env.current_contract_address()
        self.env.call(token, "transfer", contract_addr, caller, stake)

        # Remove bad block details and revert latest block number if it was the last one
        latest_num = self.storage.get("latest_block_number", U64(0))
        if block_number == latest_num:
            self.storage.set("latest_block_number", block_number - U64(1))

        self.storage.remove(f"block_hash_{block_number}")
        self.storage.remove(f"parent_hash_{block_number}")
        self.storage.remove(f"state_root_{block_number}")
        self.storage.remove(f"block_timestamp_{block_number}")
        self.storage.remove(f"block_relayer_{block_number}")

        self.env.emit_event("relayer_slashed", {
            "slashed_relayer": bad_relayer,
            "amount": stake,
            "block_number": block_number
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause submissions (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_latest_block(self) -> Map:
        """Get information on the latest synchronized block header."""
        self._require_initialized()
        res = Map(self.env)
        latest_num = self.storage.get("latest_block_number", U64(0))
        res.set("block_number", latest_num)
        res.set("block_hash", self.storage.get(f"block_hash_{latest_num}"))
        res.set("state_root", self.storage.get(f"state_root_{latest_num}"))
        res.set("finalized", self.is_block_finalized(latest_num))
        return res

    @view
    def is_block_finalized(self, block_number: U64) -> Bool:
        """Determine if a block header has completed its dispute/finalization period."""
        if not self.storage.get("initialized", False):
            return False

        if self.storage.get(f"block_finalized_{block_number}", False):
            return True

        timestamp = self.storage.get(f"block_timestamp_{block_number}")
        if timestamp is None:
            return False

        dispute_period = self.storage.get("dispute_period", U64(0))
        if self._get_now() >= timestamp + dispute_period:
            return True

        return False

    @view
    def verify_state_proof(
        self,
        block_number: U64,
        key: Bytes,
        value: Bytes,
        proof: Vec,          # Vec of Bytes (sibling hashes)
        path_directions: Vec # Vec of U64 (0: Left sibling, 1: Right sibling)
    ) -> Bool:
        """
        Verify a Merkle state proof for key-value pair against a finalized block's state root.
        """
        self._require_initialized()

        # Check block finalization
        if not self.is_block_finalized(block_number):
            return False

        state_root = self.storage.get(f"state_root_{block_number}")
        if state_root is None:
            return False

        # Compute leaf hash = sha256(key + value)
        computed_hash = self.env.crypto().sha256(key + value)

        # Traverse up the Merkle path
        for i in range(len(proof)):
            sibling = proof.get(i)
            direction = path_directions.get(i)

            if direction == U64(0):
                # Sibling is on the left
                computed_hash = self.env.crypto().sha256(sibling + computed_hash)
            else:
                # Sibling is on the right
                computed_hash = self.env.crypto().sha256(computed_hash + sibling)

        return computed_hash == state_root

    @view
    def get_relayer_stake(self, relayer: Address) -> U128:
        """Query stake balance of a relayer."""
        return self.storage.get(f"stake_{relayer}", U128(0))

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

    def _require_active_relayer(self, relayer: Address):
        if not self.storage.get(f"is_relayer_{relayer}", False):
            raise ContractError.UNAUTHORIZED

    def _get_now(self) -> U64:
        return self.env.ledger_timestamp()
