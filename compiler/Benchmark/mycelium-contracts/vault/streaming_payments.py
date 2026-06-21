"""
Streaming Payments — Continuous token streaming with pause, resume, and top-up actions.

Mycelium Smart Contract for Stellar
Allows users to create real-time token streaming payments to a beneficiary at a set flow rate
per second. Senders can pause/resume streams, top-up deposits, or cancel streams to claw back
unused funds. Beneficiaries can withdraw accrued stream earnings at any time.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    INVALID_PARAMETERS = 4
    STREAM_NOT_FOUND = 5
    STREAM_NOT_ACTIVE = 6
    STREAM_NOT_PAUSED = 7
    NO_FUNDS_TO_WITHDRAW = 8
    STREAM_COMPLETED = 9


STATUS_ACTIVE = 1
STATUS_PAUSED = 2
STATUS_CANCELED = 3


@contract
class StreamingPayments:
    """
    Continuous linear token streaming protocol supporting multi-party controls,
    deposit additions, and pause/resume lifecycle management.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """Initialize the streaming payments platform."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("stream_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin})

    @external
    def create_stream(
        self,
        sender: Address,
        recipient: Address,
        token: Address,
        flow_rate: U128,      # Amount of tokens per second
        initial_deposit: U128,
    ) -> U64:
        """Create a new token stream to recipient funded by initial_deposit."""
        sender.require_auth()
        self._require_initialized()

        if sender == recipient or flow_rate == 0 or initial_deposit == 0:
            raise ContractError.INVALID_PARAMETERS

        # Transfer tokens to this contract
        self.env.transfer(token, sender, self.env.current_contract(), initial_deposit)

        stream_id = self.storage.get("stream_count", U64(0)) + U64(1)
        self.storage.set("stream_count", stream_id)

        current_time = self.env.ledger().timestamp()

        self.storage.set(f"stream:{stream_id}:sender", sender)
        self.storage.set(f"stream:{stream_id}:recipient", recipient)
        self.storage.set(f"stream:{stream_id}:token", token)
        self.storage.set(f"stream:{stream_id}:flow_rate", flow_rate)
        self.storage.set(f"stream:{stream_id}:deposit", initial_deposit)
        self.storage.set(f"stream:{stream_id}:last_update", current_time)
        self.storage.set(f"stream:{stream_id}:paused_accrued", U128(0))
        self.storage.set(f"stream:{stream_id}:status", U64(STATUS_ACTIVE))

        self.env.emit_event("stream_created", {
            "stream_id": stream_id,
            "sender": sender,
            "recipient": recipient,
            "token": token,
            "flow_rate": flow_rate,
            "deposit": initial_deposit,
        })

        return stream_id

    @external
    def withdraw_from_stream(self, recipient: Address, stream_id: U64):
        """Allows recipient to withdraw all currently accrued tokens from the stream."""
        self._require_initialized()
        self._check_stream_exists(stream_id)

        msg_recipient = self.storage.get(f"stream:{stream_id}:recipient")
        if recipient != msg_recipient:
            raise ContractError.UNAUTHORIZED

        status = self.storage.get(f"stream:{stream_id}:status")
        if status == STATUS_CANCELED:
            raise ContractError.STREAM_COMPLETED

        current_time = self.env.ledger().timestamp()
        
        # Calculate accrued and check if dry
        info = self._calculate_accrued(stream_id, current_time)
        claimable = info["accrued"]
        is_dry = info["is_dry"]
        dry_time = info["dry_time"]

        if claimable == 0:
            raise ContractError.NO_FUNDS_TO_WITHDRAW

        token = self.storage.get(f"stream:{stream_id}:token")

        if is_dry:
            # Stream ran dry, transition to completed
            self.storage.set(f"stream:{stream_id}:status", U64(STATUS_CANCELED))
            self.storage.set(f"stream:{stream_id}:deposit", U128(0))
            self.storage.set(f"stream:{stream_id}:paused_accrued", U128(0))
            self.storage.set(f"stream:{stream_id}:last_update", dry_time)

            self.env.transfer(token, self.env.current_contract(), recipient, claimable)
            self.env.emit_event("stream_completed", {"stream_id": stream_id})
        else:
            # Normal claim
            deposit = self.storage.get(f"stream:{stream_id}:deposit")
            self.storage.set(f"stream:{stream_id}:deposit", deposit - claimable)
            self.storage.set(f"stream:{stream_id}:paused_accrued", U128(0))
            self.storage.set(f"stream:{stream_id}:last_update", current_time)

            self.env.transfer(token, self.env.current_contract(), recipient, claimable)

        self.env.emit_event("withdrawn", {
            "stream_id": stream_id,
            "recipient": recipient,
            "amount": claimable,
        })

    @external
    def pause_stream(self, sender: Address, stream_id: U64):
        """Allows the sender to pause an active stream, freezing token accrual."""
        sender.require_auth()
        self._require_initialized()
        self._check_stream_exists(stream_id)

        msg_sender = self.storage.get(f"stream:{stream_id}:sender")
        if sender != msg_sender:
            raise ContractError.UNAUTHORIZED

        status = self.storage.get(f"stream:{stream_id}:status")
        if status != STATUS_ACTIVE:
            raise ContractError.STREAM_NOT_ACTIVE

        current_time = self.env.ledger().timestamp()
        info = self._calculate_accrued(stream_id, current_time)
        claimable = info["accrued"]
        is_dry = info["is_dry"]
        dry_time = info["dry_time"]

        token = self.storage.get(f"stream:{stream_id}:token")
        
        if is_dry:
            # If it already ran dry, complete it
            self.storage.set(f"stream:{stream_id}:status", U64(STATUS_CANCELED))
            self.storage.set(f"stream:{stream_id}:deposit", U128(0))
            self.storage.set(f"stream:{stream_id}:paused_accrued", U128(0))
            self.storage.set(f"stream:{stream_id}:last_update", dry_time)

            recipient = self.storage.get(f"stream:{stream_id}:recipient")
            self.env.transfer(token, self.env.current_contract(), recipient, claimable)
            self.env.emit_event("stream_completed", {"stream_id": stream_id})
        else:
            # Deduct accrued from deposit, store it in paused_accrued
            deposit = self.storage.get(f"stream:{stream_id}:deposit")
            self.storage.set(f"stream:{stream_id}:deposit", deposit - claimable)
            self.storage.set(f"stream:{stream_id}:paused_accrued", claimable)
            self.storage.set(f"stream:{stream_id}:status", U64(STATUS_PAUSED))
            self.storage.set(f"stream:{stream_id}:last_update", current_time)

            self.env.emit_event("stream_paused", {"stream_id": stream_id})

    @external
    def resume_stream(self, sender: Address, stream_id: U64):
        """Allows the sender to resume a paused stream."""
        sender.require_auth()
        self._require_initialized()
        self._check_stream_exists(stream_id)

        msg_sender = self.storage.get(f"stream:{stream_id}:sender")
        if sender != msg_sender:
            raise ContractError.UNAUTHORIZED

        status = self.storage.get(f"stream:{stream_id}:status")
        if status != STATUS_PAUSED:
            raise ContractError.STREAM_NOT_PAUSED

        current_time = self.env.ledger().timestamp()
        
        self.storage.set(f"stream:{stream_id}:status", U64(STATUS_ACTIVE))
        self.storage.set(f"stream:{stream_id}:last_update", current_time)

        self.env.emit_event("stream_resumed", {"stream_id": stream_id})

    @external
    def deposit_to_stream(self, sender: Address, stream_id: U64, amount: U128):
        """Sender deposits additional tokens to extend the stream duration."""
        sender.require_auth()
        self._require_initialized()
        self._check_stream_exists(stream_id)

        msg_sender = self.storage.get(f"stream:{stream_id}:sender")
        if sender != msg_sender:
            raise ContractError.UNAUTHORIZED

        status = self.storage.get(f"stream:{stream_id}:status")
        if status == STATUS_CANCELED:
            raise ContractError.STREAM_COMPLETED

        # Transfer tokens to this contract
        token = self.storage.get(f"stream:{stream_id}:token")
        self.env.transfer(token, sender, self.env.current_contract(), amount)

        # Accrue any pending tokens up to now to protect flow math
        current_time = self.env.ledger().timestamp()
        info = self._calculate_accrued(stream_id, current_time)
        claimable = info["accrued"]
        is_dry = info["is_dry"]

        if is_dry:
            # If it had run dry, restart it with the new amount as deposit
            self.storage.set(f"stream:{stream_id}:deposit", amount)
            self.storage.set(f"stream:{stream_id}:paused_accrued", claimable)  # Hold what was earned before dry
            self.storage.set(f"stream:{stream_id}:last_update", current_time)
            self.storage.set(f"stream:{stream_id}:status", U64(STATUS_ACTIVE))
        else:
            # Update deposit and reset last_update
            deposit = self.storage.get(f"stream:{stream_id}:deposit")
            self.storage.set(f"stream:{stream_id}:deposit", deposit - claimable + amount)
            self.storage.set(f"stream:{stream_id}:paused_accrued", claimable)
            self.storage.set(f"stream:{stream_id}:last_update", current_time)

        self.env.emit_event("stream_topped_up", {
            "stream_id": stream_id,
            "added_amount": amount,
        })

    @external
    def cancel_stream(self, caller: Address, stream_id: U64):
        """Allows either sender or recipient to cancel the stream, returning unused funds to sender."""
        caller.require_auth()
        self._require_initialized()
        self._check_stream_exists(stream_id)

        sender = self.storage.get(f"stream:{stream_id}:sender")
        recipient = self.storage.get(f"stream:{stream_id}:recipient")

        if caller != sender and caller != recipient:
            raise ContractError.UNAUTHORIZED

        status = self.storage.get(f"stream:{stream_id}:status")
        if status == STATUS_CANCELED:
            raise ContractError.STREAM_COMPLETED

        current_time = self.env.ledger().timestamp()
        info = self._calculate_accrued(stream_id, current_time)
        claimable = info["accrued"]

        deposit = self.storage.get(f"stream:{stream_id}:deposit")
        unused_deposit = U128(0)
        if deposit > claimable:
            unused_deposit = deposit - claimable

        token = self.storage.get(f"stream:{stream_id}:token")

        # Update stream to canceled
        self.storage.set(f"stream:{stream_id}:status", U64(STATUS_CANCELED))
        self.storage.set(f"stream:{stream_id}:deposit", U128(0))
        self.storage.set(f"stream:{stream_id}:paused_accrued", U128(0))

        # Transfer payouts
        if claimable > 0:
            self.env.transfer(token, self.env.current_contract(), recipient, claimable)
        if unused_deposit > 0:
            self.env.transfer(token, self.env.current_contract(), sender, unused_deposit)

        self.env.emit_event("stream_canceled", {
            "stream_id": stream_id,
            "refunded_to_sender": unused_deposit,
            "paid_to_recipient": claimable,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_stream(self, stream_id: U64) -> Map:
        """Get the full specifications and status of a stream."""
        self._check_stream_exists(stream_id)
        return {
            "sender": self.storage.get(f"stream:{stream_id}:sender"),
            "recipient": self.storage.get(f"stream:{stream_id}:recipient"),
            "token": self.storage.get(f"stream:{stream_id}:token"),
            "flow_rate": self.storage.get(f"stream:{stream_id}:flow_rate"),
            "deposit": self.storage.get(f"stream:{stream_id}:deposit"),
            "last_update": self.storage.get(f"stream:{stream_id}:last_update"),
            "paused_accrued": self.storage.get(f"stream:{stream_id}:paused_accrued"),
            "status": self.storage.get(f"stream:{stream_id}:status"),
        }

    @view
    def get_claimable_balance(self, stream_id: U64) -> U128:
        """Get the current outstanding amount ready for recipient withdrawal."""
        if not self.storage.get(f"stream:{stream_id}:sender"):
            return U128(0)
        current_time = self.env.ledger().timestamp()
        info = self._calculate_accrued(stream_id, current_time)
        return info["accrued"]

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _check_stream_exists(self, stream_id: U64):
        total = self.storage.get("stream_count", U64(0))
        if stream_id == 0 or stream_id > total:
            raise ContractError.STREAM_NOT_FOUND

    def _calculate_accrued(self, stream_id: U64, current_time: U64) -> Map:
        status = self.storage.get(f"stream:{stream_id}:status")
        paused_accrued = self.storage.get(f"stream:{stream_id}:paused_accrued", U128(0))
        
        if status == STATUS_CANCELED:
            return {"accrued": U128(0), "is_dry": True, "dry_time": current_time}
            
        if status == STATUS_PAUSED:
            return {"accrued": paused_accrued, "is_dry": False, "dry_time": current_time}

        flow_rate = self.storage.get(f"stream:{stream_id}:flow_rate")
        deposit = self.storage.get(f"stream:{stream_id}:deposit")
        last_update = self.storage.get(f"stream:{stream_id}:last_update")

        elapsed = current_time - last_update
        accrued = U128(elapsed) * flow_rate

        if accrued >= deposit:
            # Stream ran dry
            dry_seconds = deposit // flow_rate
            dry_time = last_update + U64(dry_seconds)
            return {
                "accrued": paused_accrued + deposit,
                "is_dry": True,
                "dry_time": dry_time
            }
        else:
            return {
                "accrued": paused_accrued + accrued,
                "is_dry": False,
                "dry_time": current_time
            }
