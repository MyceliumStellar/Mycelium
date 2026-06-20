"""
Tipping Contract — Direct and batch tipping with sender/recipient fee options and rankings.

Mycelium Smart Contract for Stellar
Provides tipping features with optional message hash attachments, platform fee splits
(deducted or added), batch sends, and global leaderboards for top tippers and recipients.
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
    INSUFFICIENT_FUNDS = 5
    TRANSFER_FAILED = 6


@contract
class TippingContract:
    """
    Tipping smart contract for sending direct micro-tips or batch tipping,
    recording metadata, and maintaining top-5 leaderboards.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        base_asset: Address,
        platform_fee_bps: U64,
    ):
        """Initialize the tipping contract."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if platform_fee_bps > 2000:  # Maximum 20% platform fee
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("admin", admin)
        self.storage.set("base_asset", base_asset)
        self.storage.set("platform_fee", platform_fee_bps)
        self.storage.set("top_senders", Vec())
        self.storage.set("top_receivers", Vec())
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "base_asset": base_asset,
        })

    @external
    def update_platform_fee(self, admin: Address, new_fee_bps: U64):
        """Update platform fee in basis points."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        if new_fee_bps > 2000:
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("platform_fee", new_fee_bps)
        self.env.emit_event("fee_updated", {"fee_bps": new_fee_bps})

    @external
    def tip(
        self,
        sender: Address,
        receiver: Address,
        amount: U128,
        message_hash: Bytes,
        fee_paid_by_sender: Bool,
    ):
        """Send a single tip to a receiver with a message hash and custom fee option."""
        sender.require_auth()
        self._require_initialized()

        if sender == receiver or amount == 0:
            raise ContractError.INVALID_PARAMETERS

        self._process_tip(sender, receiver, amount, message_hash, fee_paid_by_sender)

    @external
    def batch_tip(
        self,
        sender: Address,
        receivers: Vec,
        amounts: Vec,
        message_hashes: Vec,
        fee_paid_by_sender: Bool,
    ):
        """Send multiple tips to different receivers in a single contract invocation."""
        sender.require_auth()
        self._require_initialized()

        receivers_len = len(receivers)
        if receivers_len == 0 or receivers_len != len(amounts) or receivers_len != len(message_hashes):
            raise ContractError.INVALID_PARAMETERS

        for i in range(receivers_len):
            receiver = receivers[i]
            amount = amounts[i]
            msg_hash = message_hashes[i]

            if sender == receiver or amount == 0:
                raise ContractError.INVALID_PARAMETERS

            self._process_tip(sender, receiver, amount, msg_hash, fee_paid_by_sender)

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_user_stats(self, user: Address) -> Map:
        """Get user's cumulative tipped and received totals."""
        return {
            "total_sent": self.storage.get(f"user:{user}:total_sent", U128(0)),
            "total_received": self.storage.get(f"user:{user}:total_received", U128(0)),
        }

    @view
    def get_rankings(self) -> Map:
        """Retrieve the top 5 senders and top 5 receivers leaderboard lists."""
        return {
            "top_senders": self.storage.get("top_senders"),
            "top_receivers": self.storage.get("top_receivers"),
        }

    # ── Private Helpers ───────────────────────────────────────────────

    def _process_tip(
        self,
        sender: Address,
        receiver: Address,
        amount: U128,
        message_hash: Bytes,
        fee_paid_by_sender: Bool,
    ):
        base_asset = self.storage.get("base_asset")
        admin = self.storage.get("admin")
        fee_bps = self.storage.get("platform_fee")

        fee = (amount * U128(fee_bps)) // U128(10000)

        total_from_sender = U128(0)
        net_to_receiver = U128(0)

        if fee_paid_by_sender:
            # Sender pays fee on top: sender pays (amount + fee)
            total_from_sender = amount + fee
            net_to_receiver = amount
        else:
            # Fee deducted from tip amount: receiver gets (amount - fee)
            total_from_sender = amount
            net_to_receiver = amount - fee

        # 1. Distribute Funds
        # Transfer net amount to receiver
        if net_to_receiver > 0:
            self.env.transfer(base_asset, sender, receiver, net_to_receiver)
        # Transfer fee to admin
        if fee > 0:
            self.env.transfer(base_asset, sender, admin, fee)

        # 2. Update stats and rankings
        # Update Sender Stats
        sender_sent = self.storage.get(f"user:{sender}:total_sent", U128(0))
        new_sender_sent = sender_sent + amount
        self.storage.set(f"user:{sender}:total_sent", new_sender_sent)
        self._update_leaderboard(sender, new_sender_sent, True)

        # Update Receiver Stats
        receiver_received = self.storage.get(f"user:{receiver}:total_received", U128(0))
        new_receiver_received = receiver_received + amount
        self.storage.set(f"user:{receiver}:total_received", new_receiver_received)
        self._update_leaderboard(receiver, new_receiver_received, False)

        self.env.emit_event("tip_processed", {
            "sender": sender,
            "receiver": receiver,
            "amount": amount,
            "fee": fee,
            "message_hash": message_hash,
        })

    def _update_leaderboard(self, user: Address, new_total: U128, is_sender: Bool):
        key = "top_senders" if is_sender else "top_receivers"
        leaderboard = self.storage.get(key)

        # Remove user if already exists to update their position
        filtered = Vec()
        for idx in range(len(leaderboard)):
            entry = leaderboard[idx]
            if entry.get("user") != user:
                filtered.append(entry)

        # Find insertion index to maintain descending sort order
        inserted = False
        new_leaderboard = Vec()
        new_entry = {"user": user, "total": new_total}

        for idx in range(len(filtered)):
            current = filtered[idx]
            if not inserted and new_total > current.get("total"):
                new_leaderboard.append(new_entry)
                inserted = True
            new_leaderboard.append(current)

        if not inserted:
            new_leaderboard.append(new_entry)

        # Keep only top 5 entries
        trimmed = Vec()
        limit = len(new_leaderboard)
        if limit > 5:
            limit = 5
        for idx in range(limit):
            trimmed.append(new_leaderboard[idx])

        self.storage.set(key, trimmed)

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED
