"""
Commitment Scheme — Cryptographic hash commitments with range verification and reveal window boundary checks.

Mycelium Smart Contract for Stellar
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    COMMITMENT_EXISTS = 4
    COMMITMENT_NOT_FOUND = 5
    REVEAL_WINDOW_NOT_OPEN = 6
    REVEAL_WINDOW_CLOSED = 7
    INVALID_REVEAL = 8
    OUT_OF_RANGE = 9
    ALREADY_REVEALED = 10


@contract
class CommitmentScheme:
    """Manages cryptographic commitments (hash-based commitments), reveal windows, and value range checks."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        min_value: U64,
        max_value: U64,
        reveal_delay: U64,
        reveal_duration: U64
    ):
        """Initialize the commitment scheme parameters."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        if min_value > max_value:
            raise ContractError.OUT_OF_RANGE

        self.storage.set("admin", admin)
        self.storage.set("min_value_limit", min_value)
        self.storage.set("max_value_limit", max_value)
        self.storage.set("reveal_delay_sec", reveal_delay)
        self.storage.set("reveal_duration_sec", reveal_duration)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "min_value": min_value,
            "max_value": max_value,
            "reveal_delay": reveal_delay,
            "reveal_duration": reveal_duration
        })

    # ------------------------------------------------------------------ #
    #  Admin Operations                                                   #
    # ------------------------------------------------------------------ #

    @external
    def update_parameters(
        self,
        admin: Address,
        min_value: U64,
        max_value: U64,
        reveal_delay: U64,
        reveal_duration: U64
    ):
        """Update system limits and timing parameters. Only Admin."""
        self._require_admin(admin)
        if min_value > max_value:
            raise ContractError.OUT_OF_RANGE

        self.storage.set("min_value_limit", min_value)
        self.storage.set("max_value_limit", max_value)
        self.storage.set("reveal_delay_sec", reveal_delay)
        self.storage.set("reveal_duration_sec", reveal_duration)

        self.env.emit_event("parameters_updated", {
            "min_value": min_value,
            "max_value": max_value,
            "reveal_delay": reveal_delay,
            "reveal_duration": reveal_duration
        })

    # ------------------------------------------------------------------ #
    #  User Operations                                                    #
    # ------------------------------------------------------------------ #

    @external
    def submit_commitment(self, caller: Address, commitment: Bytes):
        """Submit a new commitment hash. The commitment is typically keccak256(value + salt)."""
        self._require_initialized()
        caller.require_auth()

        # Ensure commitment doesn't exist yet
        if self.storage.get(("commitment", commitment), None) is not None:
            raise ContractError.COMMITMENT_EXISTS

        now = self.env.ledger().timestamp()

        state = {
            "owner": caller,
            "timestamp": now,
            "revealed": False,
            "value": U64(0)
        }

        self.storage.set(("commitment", commitment), state)
        self.env.emit_event("commitment_submitted", {
            "owner": caller,
            "commitment": commitment,
            "timestamp": now
        })

    @external
    def reveal_commitment(
        self,
        caller: Address,
        commitment: Bytes,
        value: U64,
        salt: Bytes
    ) -> Bool:
        """Reveal the value and salt behind a commitment. Validates timestamps and ranges."""
        self._require_initialized()
        caller.require_auth()

        state = self.storage.get(("commitment", commitment), None)
        if state is None:
            raise ContractError.COMMITMENT_NOT_FOUND
        
        if state["revealed"]:
            raise ContractError.ALREADY_REVEALED

        if state["owner"] != caller:
            raise ContractError.UNAUTHORIZED

        now = self.env.ledger().timestamp()
        commit_time = state["timestamp"]
        delay = self.storage.get("reveal_delay_sec")
        duration = self.storage.get("reveal_duration_sec")

        # Enforce reveal window boundaries
        if now < commit_time + delay:
            raise ContractError.REVEAL_WINDOW_NOT_OPEN
        if now > commit_time + delay + duration:
            raise ContractError.REVEAL_WINDOW_CLOSED

        # Range verification on value parameters
        min_limit = self.storage.get("min_value_limit")
        max_limit = self.storage.get("max_value_limit")
        if value < min_limit or value > max_limit:
            raise ContractError.OUT_OF_RANGE

        # Verify commitment hash matches keccak256(value + salt)
        # Convert value to Bytes or combine with salt using keccak256
        # Let's perform hash verification
        expected_hash = self.env.crypto().keccak256(value, salt)
        if expected_hash != commitment:
            raise ContractError.INVALID_REVEAL

        # Mark commitment as revealed
        state["revealed"] = True
        state["value"] = value
        self.storage.set(("commitment", commitment), state)

        self.env.emit_event("commitment_revealed", {
            "owner": caller,
            "commitment": commitment,
            "value": value
        })

        return True

    # ------------------------------------------------------------------ #
    #  View Operations                                                    #
    # ------------------------------------------------------------------ #

    @view
    def get_commitment_state(self, commitment: Bytes) -> Map:
        """Get the current state and parameters of a commitment."""
        self._require_initialized()
        state = self.storage.get(("commitment", commitment), None)
        if state is None:
            raise ContractError.COMMITMENT_NOT_FOUND
        
        res = Map()
        res.set(Symbol("owner"), state["owner"])
        res.set(Symbol("timestamp"), state["timestamp"])
        res.set(Symbol("revealed"), state["revealed"])
        if state["revealed"]:
            res.set(Symbol("value"), state["value"])
        else:
            res.set(Symbol("value"), U64(0))
        return res

    @view
    def check_reveal_status(self, commitment: Bytes) -> Map:
        """Check if the reveal window is open/closed for a commitment."""
        self._require_initialized()
        state = self.storage.get(("commitment", commitment), None)
        if state is None:
            raise ContractError.COMMITMENT_NOT_FOUND

        now = self.env.ledger().timestamp()
        commit_time = state["timestamp"]
        delay = self.storage.get("reveal_delay_sec")
        duration = self.storage.get("reveal_duration_sec")

        open_time = commit_time + delay
        close_time = commit_time + delay + duration

        status = Symbol("closed")
        if now < open_time:
            status = Symbol("pending")
        elif now <= close_time:
            status = Symbol("open")

        res = Map()
        res.set(Symbol("status"), status)
        res.set(Symbol("opens_at"), open_time)
        res.set(Symbol("closes_at"), close_time)
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                   #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        caller.require_auth()
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED
