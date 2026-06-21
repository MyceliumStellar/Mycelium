"""
Escrow Service — Multi-party escrow with milestone releases and arbiter-led dispute resolution.

Mycelium Smart Contract for Stellar
Enables buyers and sellers to create escrow agreements with multiple milestone release phases.
If a milestone is contested, the designated arbiter can resolve the dispute and split the funds
accordingly after charging an arbitration fee.
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
    ESCROW_NOT_FOUND = 5
    MILESTONE_NOT_FOUND = 6
    INVALID_STATUS = 7
    DISPUTE_NOT_RESOLVABLE = 8


# Milestone statuses
STATUS_PENDING = 0
STATUS_RELEASED = 1
STATUS_DISPUTED = 2
STATUS_REFUNDED = 3
STATUS_RESOLVED = 4


@contract
class EscrowService:
    """
    Escrow platform for milestone-based service agreements, supporting buyer releases,
    seller refunds, and independent arbitration.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """Initialize the escrow service platform."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("escrow_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin})

    @external
    def create_escrow(
        self,
        buyer: Address,
        seller: Address,
        arbiter: Address,
        token: Address,
        milestone_amounts: Vec,
        arbiter_fee_bps: U64,
    ) -> U64:
        """Create an escrow agreement with specified milestones and lock corresponding tokens."""
        buyer.require_auth()
        self._require_initialized()

        if buyer == seller or buyer == arbiter or seller == arbiter:
            raise ContractError.INVALID_PARAMETERS

        if len(milestone_amounts) == 0:
            raise ContractError.INVALID_PARAMETERS

        if arbiter_fee_bps > 1000:  # Max 10% arbitration fee
            raise ContractError.INVALID_PARAMETERS

        # Calculate total deposit required
        total_deposit = U128(0)
        for i in range(len(milestone_amounts)):
            amt = milestone_amounts[i]
            if amt == 0:
                raise ContractError.INVALID_PARAMETERS
            total_deposit = total_deposit + amt

        # Transfer total milestones amount to the contract
        self.env.transfer(token, buyer, self.env.current_contract(), total_deposit)

        escrow_id = self.storage.get("escrow_count", U64(0)) + U64(1)
        self.storage.set("escrow_count", escrow_id)

        self.storage.set(f"escrow:{escrow_id}:buyer", buyer)
        self.storage.set(f"escrow:{escrow_id}:seller", seller)
        self.storage.set(f"escrow:{escrow_id}:arbiter", arbiter)
        self.storage.set(f"escrow:{escrow_id}:token", token)
        self.storage.set(f"escrow:{escrow_id}:arbiter_fee_bps", arbiter_fee_bps)
        self.storage.set(f"escrow:{escrow_id}:milestone_count", U64(len(milestone_amounts)))

        # Register milestones
        for i in range(len(milestone_amounts)):
            amt = milestone_amounts[i]
            milestone_id = U64(i + 1)
            self.storage.set(f"milestone:amount:{escrow_id}:{milestone_id}", amt)
            self.storage.set(f"milestone:status:{escrow_id}:{milestone_id}", U64(STATUS_PENDING))

        self.env.emit_event("escrow_created", {
            "escrow_id": escrow_id,
            "buyer": buyer,
            "seller": seller,
            "arbiter": arbiter,
            "token": token,
            "total_deposit": total_deposit,
        })

        return escrow_id

    @external
    def release_milestone(self, buyer: Address, escrow_id: U64, milestone_id: U64):
        """Allows buyer to release a milestone payment to the seller."""
        buyer.require_auth()
        self._require_initialized()
        self._check_escrow_exists(escrow_id)
        self._check_milestone_exists(escrow_id, milestone_id)

        escrow_buyer = self.storage.get(f"escrow:{escrow_id}:buyer")
        if buyer != escrow_buyer:
            raise ContractError.UNAUTHORIZED

        status = self.storage.get(f"milestone:status:{escrow_id}:{milestone_id}")
        if status != STATUS_PENDING:
            raise ContractError.INVALID_STATUS

        amount = self.storage.get(f"milestone:amount:{escrow_id}:{milestone_id}")
        seller = self.storage.get(f"escrow:{escrow_id}:seller")
        token = self.storage.get(f"escrow:{escrow_id}:token")

        # Update status
        self.storage.set(f"milestone:status:{escrow_id}:{milestone_id}", U64(STATUS_RELEASED))

        # Transfer tokens to seller
        self.env.transfer(token, self.env.current_contract(), seller, amount)

        self.env.emit_event("milestone_released", {
            "escrow_id": escrow_id,
            "milestone_id": milestone_id,
            "amount": amount,
        })

    @external
    def refund_milestone(self, seller: Address, escrow_id: U64, milestone_id: U64):
        """Allows seller to waive rights to a milestone, refunding the buyer."""
        seller.require_auth()
        self._require_initialized()
        self._check_escrow_exists(escrow_id)
        self._check_milestone_exists(escrow_id, milestone_id)

        escrow_seller = self.storage.get(f"escrow:{escrow_id}:seller")
        if seller != escrow_seller:
            raise ContractError.UNAUTHORIZED

        status = self.storage.get(f"milestone:status:{escrow_id}:{milestone_id}")
        if status != STATUS_PENDING:
            raise ContractError.INVALID_STATUS

        amount = self.storage.get(f"milestone:amount:{escrow_id}:{milestone_id}")
        buyer = self.storage.get(f"escrow:{escrow_id}:buyer")
        token = self.storage.get(f"escrow:{escrow_id}:token")

        # Update status
        self.storage.set(f"milestone:status:{escrow_id}:{milestone_id}", U64(STATUS_REFUNDED))

        # Refund tokens to buyer
        self.env.transfer(token, self.env.current_contract(), buyer, amount)

        self.env.emit_event("milestone_refunded", {
            "escrow_id": escrow_id,
            "milestone_id": milestone_id,
            "amount": amount,
        })

    @external
    def dispute_milestone(self, caller: Address, escrow_id: U64, milestone_id: U64):
        """Buyer or seller flags a milestone as disputed, locked until arbiter intervenes."""
        caller.require_auth()
        self._require_initialized()
        self._check_escrow_exists(escrow_id)
        self._check_milestone_exists(escrow_id, milestone_id)

        buyer = self.storage.get(f"escrow:{escrow_id}:buyer")
        seller = self.storage.get(f"escrow:{escrow_id}:seller")

        if caller != buyer and caller != seller:
            raise ContractError.UNAUTHORIZED

        status = self.storage.get(f"milestone:status:{escrow_id}:{milestone_id}")
        if status != STATUS_PENDING:
            raise ContractError.INVALID_STATUS

        self.storage.set(f"milestone:status:{escrow_id}:{milestone_id}", U64(STATUS_DISPUTED))

        self.env.emit_event("milestone_disputed", {
            "escrow_id": escrow_id,
            "milestone_id": milestone_id,
            "by": caller,
        })

    @external
    def resolve_dispute(
        self,
        arbiter: Address,
        escrow_id: U64,
        milestone_id: U64,
        buyer_share_pct: U64,  # e.g., 40 for 40%
        seller_share_pct: U64, # e.g., 60 for 60%
    ):
        """Allows designated arbiter to split disputed milestone funds, claiming a resolution fee."""
        arbiter.require_auth()
        self._require_initialized()
        self._check_escrow_exists(escrow_id)
        self._check_milestone_exists(escrow_id, milestone_id)

        escrow_arbiter = self.storage.get(f"escrow:{escrow_id}:arbiter")
        if arbiter != escrow_arbiter:
            raise ContractError.UNAUTHORIZED

        status = self.storage.get(f"milestone:status:{escrow_id}:{milestone_id}")
        if status != STATUS_DISPUTED:
            raise ContractError.DISPUTE_NOT_RESOLVABLE

        if buyer_share_pct + seller_share_pct != 100:
            raise ContractError.INVALID_PARAMETERS

        amount = self.storage.get(f"milestone:amount:{escrow_id}:{milestone_id}")
        token = self.storage.get(f"escrow:{escrow_id}:token")
        buyer = self.storage.get(f"escrow:{escrow_id}:buyer")
        seller = self.storage.get(f"escrow:{escrow_id}:seller")

        # 1. Calculate arbiter fee
        fee_bps = self.storage.get(f"escrow:{escrow_id}:arbiter_fee_bps")
        arbiter_fee = (amount * U128(fee_bps)) // U128(10000)
        net_amount = amount - arbiter_fee

        # 2. Split remainder based on arbiter decision
        buyer_payout = (net_amount * U128(buyer_share_pct)) // U128(100)
        seller_payout = net_amount - buyer_payout

        # Update status
        self.storage.set(f"milestone:status:{escrow_id}:{milestone_id}", U64(STATUS_RESOLVED))

        # 3. Perform transfers
        if arbiter_fee > 0:
            self.env.transfer(token, self.env.current_contract(), arbiter, arbiter_fee)
        if buyer_payout > 0:
            self.env.transfer(token, self.env.current_contract(), buyer, buyer_payout)
        if seller_payout > 0:
            self.env.transfer(token, self.env.current_contract(), seller, seller_payout)

        self.env.emit_event("dispute_resolved", {
            "escrow_id": escrow_id,
            "milestone_id": milestone_id,
            "buyer_payout": buyer_payout,
            "seller_payout": seller_payout,
            "arbiter_fee": arbiter_fee,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_escrow(self, escrow_id: U64) -> Map:
        """Get overall escrow details."""
        self._check_escrow_exists(escrow_id)
        return {
            "buyer": self.storage.get(f"escrow:{escrow_id}:buyer"),
            "seller": self.storage.get(f"escrow:{escrow_id}:seller"),
            "arbiter": self.storage.get(f"escrow:{escrow_id}:arbiter"),
            "token": self.storage.get(f"escrow:{escrow_id}:token"),
            "arbiter_fee_bps": self.storage.get(f"escrow:{escrow_id}:arbiter_fee_bps"),
            "milestone_count": self.storage.get(f"escrow:{escrow_id}:milestone_count"),
        }

    @view
    def get_milestone(self, escrow_id: U64, milestone_id: U64) -> Map:
        """Get details for a specific milestone."""
        self._check_escrow_exists(escrow_id)
        self._check_milestone_exists(escrow_id, milestone_id)
        return {
            "amount": self.storage.get(f"milestone:amount:{escrow_id}:{milestone_id}"),
            "status": self.storage.get(f"milestone:status:{escrow_id}:{milestone_id}"),
        }

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _check_escrow_exists(self, escrow_id: U64):
        total = self.storage.get("escrow_count", U64(0))
        if escrow_id == 0 or escrow_id > total:
            raise ContractError.ESCROW_NOT_FOUND

    def _check_milestone_exists(self, escrow_id: U64, milestone_id: U64):
        count = self.storage.get(f"escrow:{escrow_id}:milestone_count", U64(0))
        if milestone_id == 0 or milestone_id > count:
            raise ContractError.MILESTONE_NOT_FOUND
