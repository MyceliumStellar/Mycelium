"""
Reverse Auction — Descending price bids by suppliers, buyer requests, evaluation locks, dispute checks.

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
    AUCTION_NOT_STARTED = 4
    AUCTION_ENDED = 5
    AUCTION_ACTIVE = 6
    BID_TOO_HIGH = 7
    ALREADY_FINALIZED = 8
    NOT_FINALIZED = 9
    STATE_NOT_DELIVERY = 10
    STATE_NOT_DISPUTED = 11
    INSUFFICIENT_BALANCE = 12
    ZERO_AMOUNT = 13
    DELIVERY_EXPIRED = 14


class AuctionState:
    BIDDING = 0
    DELIVERY = 1
    COMPLETED = 2
    DISPUTED = 3


@contract
class ReverseAuction:
    """A reverse auction contract where suppliers compete to provide services by bidding lower prices."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        buyer: Address,
        payment_token: Address,
        max_price: U128,
        bidding_duration: U64,
        delivery_duration: U64,
        performance_bond: U128,
    ):
        """Initialize the reverse auction and lock buyer funds.

        Args:
            admin: Admin/Arbitrator address.
            buyer: Buyer requesting the service.
            payment_token: Collateral payment token.
            max_price: Max price the buyer is willing to pay.
            bidding_duration: Bidding phase duration in seconds.
            delivery_duration: Time allowed for service delivery after auction end.
            performance_bond: Performance bond required from bidding suppliers.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if max_price == U128(0):
            raise ContractError.ZERO_AMOUNT

        # Escrow buyer's max budget
        buyer.require_auth()
        success = self.env.invoke_contract(
            payment_token,
            "transfer",
            [buyer, self.env.current_contract_address(), max_price]
        )
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        self.storage.set("admin", admin)
        self.storage.set("buyer", buyer)
        self.storage.set("payment_token", payment_token)
        self.storage.set("max_price", max_price)
        self.storage.set("bidding_end_time", self.env.ledger().timestamp() + bidding_duration)
        self.storage.set("delivery_duration", delivery_duration)
        self.storage.set("performance_bond", performance_bond)

        self.storage.set("lowest_bid", max_price)
        self.storage.set("lowest_bidder", Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"))
        self.storage.set("state", AuctionState.BIDDING)
        self.storage.set("delivery_deadline", U64(0))

        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "buyer": buyer,
            "max_price": max_price,
            "end_time": self.env.ledger().timestamp() + bidding_duration,
        })

    @external
    def place_bid(self, supplier: Address, bid_price: U128) -> Bool:
        """Place a lower price bid to supply the service.

        Args:
            supplier: Supplier address.
            bid_price: Descending bid price.
        """
        self._require_initialized()
        self._require_state(AuctionState.BIDDING)
        supplier.require_auth()

        now = self.env.ledger().timestamp()
        if now >= self.storage.get("bidding_end_time"):
            raise ContractError.AUCTION_ENDED

        lowest_bid = self.storage.get("lowest_bid")
        if bid_price >= lowest_bid:
            raise ContractError.BID_TOO_HIGH

        payment_token = self.storage.get("payment_token")
        bond = self.storage.get("performance_bond")

        # Escrow performance bond from new supplier
        if bond > U128(0):
            success = self.env.invoke_contract(
                payment_token,
                "transfer",
                [supplier, self.env.current_contract_address(), bond]
            )
            if not success:
                raise ContractError.INSUFFICIENT_BALANCE

        # Refund previous lowest bidder their performance bond
        prev_bidder = self.storage.get("lowest_bidder")
        null_addr = Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF")

        if prev_bidder != null_addr and bond > U128(0):
            self.env.invoke_contract(
                payment_token,
                "transfer",
                [self.env.current_contract_address(), prev_bidder, bond]
            )

        self.storage.set("lowest_bid", bid_price)
        self.storage.set("lowest_bidder", supplier)

        self.env.emit_event("bid_placed", {
            "supplier": supplier,
            "price": bid_price,
        })

        return True

    @external
    def finalize_auction(self, caller: Address) -> Bool:
        """Finalize bidding phase, release buyer refund surplus, and enter delivery phase.

        Args:
            caller: Triggerer.
        """
        self._require_initialized()
        self._require_state(AuctionState.BIDDING)
        caller.require_auth()

        now = self.env.ledger().timestamp()
        if now < self.storage.get("bidding_end_time"):
            raise ContractError.AUCTION_ACTIVE

        buyer = self.storage.get("buyer")
        lowest_bid = self.storage.get("lowest_bid")
        max_price = self.storage.get("max_price")
        lowest_bidder = self.storage.get("lowest_bidder")
        payment_token = self.storage.get("payment_token")

        null_addr = Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF")

        if lowest_bidder == null_addr:
            # No bids: refund buyer full amount and complete
            self.storage.set("state", AuctionState.COMPLETED)
            self.env.invoke_contract(
                payment_token,
                "transfer",
                [self.env.current_contract_address(), buyer, max_price]
            )
            self.env.emit_event("completed_no_bids", {"buyer": buyer})
            return True

        # Refund buyer surplus difference immediately
        surplus = max_price - lowest_bid
        if surplus > U128(0):
            self.env.invoke_contract(
                payment_token,
                "transfer",
                [self.env.current_contract_address(), buyer, surplus]
            )

        # Enter delivery phase
        self.storage.set("state", AuctionState.DELIVERY)
        duration = self.storage.get("delivery_duration")
        self.storage.set("delivery_deadline", now + duration)

        self.env.emit_event("entered_delivery", {
            "winner": lowest_bidder,
            "final_price": lowest_bid,
            "deadline": now + duration,
        })

        return True

    @external
    def approve_delivery(self, buyer: Address) -> Bool:
        """Buyer approves completed service, releasing payment and performance bond to supplier.

        Args:
            buyer: Buyer address.
        """
        self._require_initialized()
        self._require_state(AuctionState.DELIVERY)
        buyer.require_auth()

        expected_buyer = self.storage.get("buyer")
        if buyer != expected_buyer:
            raise ContractError.UNAUTHORIZED

        self.storage.set("state", AuctionState.COMPLETED)

        payment_token = self.storage.get("payment_token")
        supplier = self.storage.get("lowest_bidder")
        winning_bid = self.storage.get("lowest_bid")
        bond = self.storage.get("performance_bond")

        total_release = winning_bid + bond
        self.env.invoke_contract(
            payment_token,
            "transfer",
            [self.env.current_contract_address(), supplier, total_release]
        )

        self.env.emit_event("delivery_approved", {
            "supplier": supplier,
            "payout": total_release,
        })

        return True

    @external
    def raise_dispute(self, caller: Address) -> Bool:
        """Raise a dispute if there is delivery failure or quality issues. Only buyer or supplier.

        Args:
            caller: Initiator of dispute.
        """
        self._require_initialized()
        self._require_state(AuctionState.DELIVERY)
        caller.require_auth()

        buyer = self.storage.get("buyer")
        supplier = self.storage.get("lowest_bidder")

        if caller != buyer and caller != supplier:
            raise ContractError.UNAUTHORIZED

        self.storage.set("state", AuctionState.DISPUTED)
        self.env.emit_event("dispute_raised", {"by": caller})

        return True

    @external
    def resolve_dispute(self, admin: Address, supplier_share: U128, buyer_share: U128) -> Bool:
        """Resolve a dispute and split the locked funds (winning bid + performance bond). Only admin.

        Args:
            admin: Admin address.
            supplier_share: Amount of token to transfer to supplier.
            buyer_share: Amount of token to transfer to buyer.
        """
        self._require_initialized()
        self._require_state(AuctionState.DISPUTED)
        admin.require_auth()

        expected_admin = self.storage.get("admin")
        if admin != expected_admin:
            raise ContractError.UNAUTHORIZED

        winning_bid = self.storage.get("lowest_bid")
        bond = self.storage.get("performance_bond")
        total_locked = winning_bid + bond

        if supplier_share + buyer_share != total_locked:
            raise ContractError.BID_TOO_HIGH

        self.storage.set("state", AuctionState.COMPLETED)

        payment_token = self.storage.get("payment_token")
        buyer = self.storage.get("buyer")
        supplier = self.storage.get("lowest_bidder")

        if supplier_share > U128(0):
            self.env.invoke_contract(
                payment_token,
                "transfer",
                [self.env.current_contract_address(), supplier, supplier_share]
            )

        if buyer_share > U128(0):
            self.env.invoke_contract(
                payment_token,
                "transfer",
                [self.env.current_contract_address(), buyer, buyer_share]
            )

        self.env.emit_event("dispute_resolved", {
            "supplier_share": supplier_share,
            "buyer_share": buyer_share,
        })

        return True

    @view
    def get_status(self) -> Map:
        """Get the reverse auction state and progress details."""
        res = Map()
        res.set("state", self.storage.get("state"))
        res.set("lowest_bidder", self.storage.get("lowest_bidder"))
        res.set("lowest_bid", self.storage.get("lowest_bid"))
        res.set("delivery_deadline", self.storage.get("delivery_deadline"))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_state(self, expected: int):
        current_state = self.storage.get("state")
        if current_state != expected:
            raise ContractError.ALREADY_FINALIZED
