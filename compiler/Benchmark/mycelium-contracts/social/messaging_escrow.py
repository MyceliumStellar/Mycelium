"""
Messaging Escrow — Pay-to-message escrow locks with spam rate filtering and stakes.

Mycelium Smart Contract for Stellar
Provides secure messaging economics, requiring senders to lock a payment and a spam-prevention
stake. Recipients can claim the payment upon reply, and the stake is returned to the sender.
If the recipient fails to reply, the sender can retrieve their locked funds. If the recipient
flags the message as spam, the stake is forfeited to the recipient or the treasury.
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
    MESSAGE_NOT_FOUND = 5
    INVALID_STATUS = 6
    TIMEOUT_NOT_REACHED = 7
    INSUFFICIENT_PAYMENT = 8
    INSUFFICIENT_STAKE = 9
    RATE_LIMIT_EXCEEDED = 10
    TRANSFER_FAILED = 11


# Message Status constants
STATUS_NONE = 0
STATUS_PENDING = 1
STATUS_COMPLETED = 2
STATUS_REJECTED = 3
STATUS_REFUNDED = 4

# Rate limit defaults
DEFAULT_WINDOW_SECONDS = 3600  # 1 hour
DEFAULT_MAX_MESSAGES = 5       # Max 5 free-rate messages per hour


@contract
class MessagingEscrow:
    """
    Escrows payments and stakes for decentralized messaging to prevent spam
    and incentivize prompt replies.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        payment_token: Address,
        fee_recipient: Address,
        protocol_fee_bps: U64,
    ):
        """Initialize the messaging escrow platform."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if protocol_fee_bps > 2000:  # Max 20% protocol fee
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("admin", admin)
        self.storage.set("payment_token", payment_token)
        self.storage.set("fee_recipient", fee_recipient)
        self.storage.set("protocol_fee_bps", protocol_fee_bps)
        self.storage.set("message_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "payment_token": payment_token,
            "fee_recipient": fee_recipient,
            "protocol_fee_bps": protocol_fee_bps,
        })

    @external
    def set_recipient_profile(
        self,
        recipient: Address,
        min_price: U128,
        min_stake: U128,
        response_timeout: U64,
        max_messages_per_hour: U64,
    ):
        """Set or update a recipient's messaging price, minimum stake, and preferences."""
        recipient.require_auth()
        self._require_initialized()

        # Enforce minimum timeout of 1 hour, maximum of 30 days
        if response_timeout < 3600 or response_timeout > 2592000:
            raise ContractError.INVALID_PARAMETERS

        # Max messages per hour must be at least 1 to avoid locking profile
        if max_messages_per_hour == 0:
            raise ContractError.INVALID_PARAMETERS

        self.storage.set(f"profile:{recipient}:min_price", min_price)
        self.storage.set(f"profile:{recipient}:min_stake", min_stake)
        self.storage.set(f"profile:{recipient}:response_timeout", response_timeout)
        self.storage.set(f"profile:{recipient}:max_messages", max_messages_per_hour)
        self.storage.set(f"profile:{recipient}:exists", True)

        self.env.emit_event("profile_updated", {
            "recipient": recipient,
            "min_price": min_price,
            "min_stake": min_stake,
            "response_timeout": response_timeout,
            "max_messages": max_messages_per_hour,
        })

    @external
    def send_message(
        self,
        sender: Address,
        recipient: Address,
        message_hash: Bytes,
        pay_amount: U128,
        stake_amount: U128,
    ) -> U64:
        """Lock payment and stake in escrow to send a message to a recipient."""
        sender.require_auth()
        self._require_initialized()

        if sender == recipient:
            raise ContractError.INVALID_PARAMETERS

        # Fetch profile parameters or use defaults
        min_price = U128(0)
        min_stake = U128(0)
        max_messages = U64(DEFAULT_MAX_MESSAGES)
        
        if self.storage.get(f"profile:{recipient}:exists", False):
            min_price = self.storage.get(f"profile:{recipient}:min_price")
            min_stake = self.storage.get(f"profile:{recipient}:min_stake")
            max_messages = self.storage.get(f"profile:{recipient}:max_messages")

        # Verify payment amount
        if pay_amount < min_price:
            raise ContractError.INSUFFICIENT_PAYMENT

        # Spam rate filter check
        current_time = self.env.ledger().timestamp()
        window_start = self.storage.get(f"rate:{sender}:{recipient}:window_start", U64(0))
        msg_count = self.storage.get(f"rate:{sender}:{recipient}:count", U64(0))

        if current_time - window_start > DEFAULT_WINDOW_SECONDS:
            # Reset window
            self.storage.set(f"rate:{sender}:{recipient}:window_start", current_time)
            msg_count = U64(1)
        else:
            msg_count = msg_count + U64(1)

        self.storage.set(f"rate:{sender}:{recipient}:count", msg_count)

        # Apply stake penalty multiplier if user is messaging rapidly
        required_stake = min_stake
        if msg_count > max_messages:
            # Over rate limit: double the required stake
            required_stake = min_stake * U128(2)
            # If sending way too many messages (e.g. triple limit), block completely
            if msg_count > max_messages * U64(3):
                raise ContractError.RATE_LIMIT_EXCEEDED

        if stake_amount < required_stake:
            raise ContractError.INSUFFICIENT_STAKE

        # Transfer tokens to the contract escrow
        total_transfer = pay_amount + stake_amount
        payment_token = self.storage.get("payment_token")
        
        if total_transfer > 0:
            self.env.transfer(payment_token, sender, self.env.current_contract(), total_transfer)

        # Record escrow details
        message_id = self.storage.get("message_count", U64(0)) + U64(1)
        self.storage.set("message_count", message_id)

        self.storage.set(f"escrow:{message_id}:sender", sender)
        self.storage.set(f"escrow:{message_id}:recipient", recipient)
        self.storage.set(f"escrow:{message_id}:pay_amount", pay_amount)
        self.storage.set(f"escrow:{message_id}:stake_amount", stake_amount)
        self.storage.set(f"escrow:{message_id}:timestamp", current_time)
        self.storage.set(f"escrow:{message_id}:status", U64(STATUS_PENDING))
        self.storage.set(f"escrow:{message_id}:hash", message_hash)

        self.env.emit_event("message_sent", {
            "message_id": message_id,
            "sender": sender,
            "recipient": recipient,
            "pay_amount": pay_amount,
            "stake_amount": stake_amount,
            "message_hash": message_hash,
        })

        return message_id

    @external
    def confirm_reply(self, recipient: Address, message_id: U64):
        """Recipient confirms they replied, releasing payment (minus fee) and returning stake to sender."""
        recipient.require_auth()
        self._require_initialized()

        self._check_message_exists(message_id)
        
        msg_recipient = self.storage.get(f"escrow:{message_id}:recipient")
        if recipient != msg_recipient:
            raise ContractError.UNAUTHORIZED

        status = self.storage.get(f"escrow:{message_id}:status")
        if status != STATUS_PENDING:
            raise ContractError.INVALID_STATUS

        sender = self.storage.get(f"escrow:{message_id}:sender")
        pay_amount = self.storage.get(f"escrow:{message_id}:pay_amount")
        stake_amount = self.storage.get(f"escrow:{message_id}:stake_amount")
        payment_token = self.storage.get("payment_token")

        # 1. Update status to completed
        self.storage.set(f"escrow:{message_id}:status", U64(STATUS_COMPLETED))

        # 2. Release payment (minus fee) to recipient, fee to protocol recipient
        protocol_fee_bps = self.storage.get("protocol_fee_bps")
        protocol_fee = (pay_amount * U128(protocol_fee_bps)) // U128(10000)
        net_payment = pay_amount - protocol_fee

        if net_payment > 0:
            self.env.transfer(payment_token, self.env.current_contract(), recipient, net_payment)

        if protocol_fee > 0:
            fee_recipient = self.storage.get("fee_recipient")
            self.env.transfer(payment_token, self.env.current_contract(), fee_recipient, protocol_fee)

        # 3. Return stake to sender
        if stake_amount > 0:
            self.env.transfer(payment_token, self.env.current_contract(), sender, stake_amount)

        self.env.emit_event("reply_confirmed", {
            "message_id": message_id,
            "recipient": recipient,
            "net_payment": net_payment,
            "returned_stake": stake_amount,
        })

    @external
    def reject_message(self, recipient: Address, message_id: U64):
        """Recipient rejects the message (spammer). Refund payment to sender, but forfeit stake to recipient."""
        recipient.require_auth()
        self._require_initialized()

        self._check_message_exists(message_id)

        msg_recipient = self.storage.get(f"escrow:{message_id}:recipient")
        if recipient != msg_recipient:
            raise ContractError.UNAUTHORIZED

        status = self.storage.get(f"escrow:{message_id}:status")
        if status != STATUS_PENDING:
            raise ContractError.INVALID_STATUS

        sender = self.storage.get(f"escrow:{message_id}:sender")
        pay_amount = self.storage.get(f"escrow:{message_id}:pay_amount")
        stake_amount = self.storage.get(f"escrow:{message_id}:stake_amount")
        payment_token = self.storage.get("payment_token")

        # 1. Update status to rejected
        self.storage.set(f"escrow:{message_id}:status", U64(STATUS_REJECTED))

        # 2. Refund the base payment to the sender
        if pay_amount > 0:
            self.env.transfer(payment_token, self.env.current_contract(), sender, pay_amount)

        # 3. Stake is split: 90% to recipient as compensation, 10% to protocol treasury
        if stake_amount > 0:
            protocol_cut = (stake_amount * U128(1000)) // U128(10000)  # 10%
            recipient_cut = stake_amount - protocol_cut

            if recipient_cut > 0:
                self.env.transfer(payment_token, self.env.current_contract(), recipient, recipient_cut)
            if protocol_cut > 0:
                fee_recipient = self.storage.get("fee_recipient")
                self.env.transfer(payment_token, self.env.current_contract(), fee_recipient, protocol_cut)

        self.env.emit_event("message_rejected", {
            "message_id": message_id,
            "recipient": recipient,
            "refunded_payment": pay_amount,
            "forfeited_stake": stake_amount,
        })

    @external
    def claim_refund(self, sender: Address, message_id: U64):
        """Sender claims a full refund if recipient does not confirm/reject before the timeout."""
        sender.require_auth()
        self._require_initialized()

        self._check_message_exists(message_id)

        msg_sender = self.storage.get(f"escrow:{message_id}:sender")
        if sender != msg_sender:
            raise ContractError.UNAUTHORIZED

        status = self.storage.get(f"escrow:{message_id}:status")
        if status != STATUS_PENDING:
            raise ContractError.INVALID_STATUS

        # Check response timeout
        recipient = self.storage.get(f"escrow:{message_id}:recipient")
        timeout = U64(259200)  # 3 days default
        if self.storage.get(f"profile:{recipient}:exists", False):
            timeout = self.storage.get(f"profile:{recipient}:response_timeout")

        timestamp = self.storage.get(f"escrow:{message_id}:timestamp")
        current_time = self.env.ledger().timestamp()

        if current_time < timestamp + timeout:
            raise ContractError.TIMEOUT_NOT_REACHED

        # 1. Update status to refunded
        self.storage.set(f"escrow:{message_id}:status", U64(STATUS_REFUNDED))

        # 2. Refund both payment and stake to sender
        pay_amount = self.storage.get(f"escrow:{message_id}:pay_amount")
        stake_amount = self.storage.get(f"escrow:{message_id}:stake_amount")
        total_refund = pay_amount + stake_amount
        payment_token = self.storage.get("payment_token")

        if total_refund > 0:
            self.env.transfer(payment_token, self.env.current_contract(), sender, total_refund)

        self.env.emit_event("message_refunded", {
            "message_id": message_id,
            "sender": sender,
            "refund_amount": total_refund,
        })

    @external
    def update_protocol_settings(
        self,
        admin: Address,
        new_fee_recipient: Address,
        new_protocol_fee_bps: U64,
    ):
        """Update global fee settings."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        if new_protocol_fee_bps > 2000:
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("fee_recipient", new_fee_recipient)
        self.storage.set("protocol_fee_bps", new_protocol_fee_bps)

        self.env.emit_event("settings_updated", {
            "fee_recipient": new_fee_recipient,
            "protocol_fee_bps": new_protocol_fee_bps,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_recipient_profile(self, recipient: Address) -> Map:
        """Get the profile config for a recipient."""
        if not self.storage.get(f"profile:{recipient}:exists", False):
            return {
                "exists": False,
                "min_price": U128(0),
                "min_stake": U128(0),
                "response_timeout": U64(259200),
                "max_messages": U64(DEFAULT_MAX_MESSAGES),
            }
        return {
            "exists": True,
            "min_price": self.storage.get(f"profile:{recipient}:min_price"),
            "min_stake": self.storage.get(f"profile:{recipient}:min_stake"),
            "response_timeout": self.storage.get(f"profile:{recipient}:response_timeout"),
            "max_messages": self.storage.get(f"profile:{recipient}:max_messages"),
        }

    @view
    def get_message_escrow(self, message_id: U64) -> Map:
        """Get details about a specific message escrow."""
        self._check_message_exists(message_id)
        return {
            "sender": self.storage.get(f"escrow:{message_id}:sender"),
            "recipient": self.storage.get(f"escrow:{message_id}:recipient"),
            "pay_amount": self.storage.get(f"escrow:{message_id}:pay_amount"),
            "stake_amount": self.storage.get(f"escrow:{message_id}:stake_amount"),
            "timestamp": self.storage.get(f"escrow:{message_id}:timestamp"),
            "status": self.storage.get(f"escrow:{message_id}:status"),
            "hash": self.storage.get(f"escrow:{message_id}:hash"),
        }

    @view
    def get_sender_rate_info(self, sender: Address, recipient: Address) -> Map:
        """Check current messaging rate status between sender and recipient."""
        current_time = self.env.ledger().timestamp()
        window_start = self.storage.get(f"rate:{sender}:{recipient}:window_start", U64(0))
        msg_count = self.storage.get(f"rate:{sender}:{recipient}:count", U64(0))

        if current_time - window_start > DEFAULT_WINDOW_SECONDS:
            return {"count": U64(0), "window_remaining": U64(0)}
        else:
            remaining = DEFAULT_WINDOW_SECONDS - (current_time - window_start)
            return {"count": msg_count, "window_remaining": remaining}

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _check_message_exists(self, message_id: U64):
        total = self.storage.get("message_count", U64(0))
        if message_id == 0 or message_id > total:
            raise ContractError.MESSAGE_NOT_FOUND
