"""
English Auction — Ascending bids, reserve limits, bid extension timers, refund overrides.

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
    BID_TOO_LOW = 6
    RESERVE_NOT_MET = 7
    AUCTION_NOT_ENDED = 8
    INSUFFICIENT_BALANCE = 9
    ZERO_AMOUNT = 10


@contract
class EnglishAuction:
    """A contract for managing an ascending English auction with dynamic bid extensions."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        seller: Address,
        asset_token: Address,
        asset_amount: U128,
        collateral_token: Address,
        reserve_price: U128,
        min_increment: U128,
        start_time: U64,
        duration: U64,
        extension_window: U64,
    ):
        """Initialize the English auction.

        Args:
            admin: Admin address with emergency refund override powers.
            seller: Address of the asset seller.
            asset_token: Token address of the auctioned asset.
            asset_amount: Amount of asset tokens being auctioned.
            collateral_token: Token address used for bids.
            reserve_price: Minimum bid price required for a successful sale.
            min_increment: Minimum amount the next bid must exceed the current highest bid.
            start_time: Timestamp when bidding starts.
            duration: Duration of the auction in seconds.
            extension_window: Seconds before end_time where a bid extends the auction.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("seller", seller)
        self.storage.set("asset_token", asset_token)
        self.storage.set("asset_amount", asset_amount)
        self.storage.set("collateral_token", collateral_token)
        self.storage.set("reserve_price", reserve_price)
        self.storage.set("min_increment", min_increment)
        self.storage.set("start_time", start_time)
        self.storage.set("end_time", start_time + duration)
        self.storage.set("extension_window", extension_window)

        self.storage.set("highest_bid", U128(0))
        self.storage.set("highest_bidder", Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"))
        self.storage.set("finalized", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "seller": seller,
            "asset_token": asset_token,
            "reserve_price": reserve_price,
            "end_time": start_time + duration,
        })

    @external
    def place_bid(self, bidder: Address, bid_amount: U128) -> U128:
        """Place a higher ascending bid.

        Args:
            bidder: Bidder address.
            bid_amount: Amount of collateral token.
        """
        self._require_initialized()
        bidder.require_auth()

        now = self.env.ledger().timestamp()
        start = self.storage.get("start_time")
        end = self.storage.get("end_time")

        if now < start:
            raise ContractError.AUCTION_NOT_STARTED
        if now >= end:
            raise ContractError.AUCTION_ENDED

        highest_bid = self.storage.get("highest_bid")
        min_increment = self.storage.get("min_increment")

        # Bid validation
        if highest_bid == U128(0):
            # First bid must meet reserve price
            if bid_amount < self.storage.get("reserve_price"):
                raise ContractError.BID_TOO_LOW
        else:
            if bid_amount < highest_bid + min_increment:
                raise ContractError.BID_TOO_LOW

        collateral_token = self.storage.get("collateral_token")
        
        # Transfer new bid to contract
        success = self.env.invoke_contract(
            collateral_token,
            "transfer",
            [bidder, self.env.current_contract_address(), bid_amount]
        )
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        # Refund previous highest bidder
        previous_bidder = self.storage.get("highest_bidder")
        previous_bid = highest_bid

        self.storage.set("highest_bid", bid_amount)
        self.storage.set("highest_bidder", bidder)

        if previous_bid > U128(0):
            # Attempt to refund. If it fails, log event and allow admin to override manually
            refund_success = self.env.invoke_contract(
                collateral_token,
                "transfer",
                [self.env.current_contract_address(), previous_bidder, previous_bid]
            )
            if not refund_success:
                # Store failed refund in state to let admin release manually
                self.storage.set(("pending_refund", previous_bidder), previous_bid)
                self.env.emit_event("refund_failed", {
                    "bidder": previous_bidder,
                    "amount": previous_bid,
                })

        # Bid extension timer check (anti-sniping)
        extension_window = self.storage.get("extension_window")
        if end - now < extension_window:
            new_end = now + extension_window
            self.storage.set("end_time", new_end)
            self.env.emit_event("auction_extended", {"new_end_time": new_end})

        self.env.emit_event("bid_placed", {
            "bidder": bidder,
            "amount": bid_amount,
        })

        return bid_amount

    @external
    def finalize(self, caller: Address):
        """Finalize the auction after expiration. Transfers asset to winner, collateral to seller."""
        self._require_initialized()
        caller.require_auth()

        now = self.env.ledger().timestamp()
        end = self.storage.get("end_time")

        if now < end:
            raise ContractError.AUCTION_NOT_ENDED

        if self.storage.get("finalized", False):
            raise ContractError.AUCTION_ENDED

        self.storage.set("finalized", True)

        seller = self.storage.get("seller")
        asset_token = self.storage.get("asset_token")
        asset_amount = self.storage.get("asset_amount")
        collateral_token = self.storage.get("collateral_token")

        highest_bid = self.storage.get("highest_bid")
        highest_bidder = self.storage.get("highest_bidder")
        reserve = self.storage.get("reserve_price")

        if highest_bid >= reserve:
            # Transfer asset to highest bidder
            self.env.invoke_contract(
                asset_token,
                "transfer",
                [self.env.current_contract_address(), highest_bidder, asset_amount]
            )
            # Transfer bid funds to seller
            self.env.invoke_contract(
                collateral_token,
                "transfer",
                [self.env.current_contract_address(), seller, highest_bid]
            )
            self.env.emit_event("auction_finalized", {
                "winner": highest_bidder,
                "payout": highest_bid,
                "reserve_met": True,
            })
        else:
            # Reserve not met (or no bids): return asset to seller
            self.env.invoke_contract(
                asset_token,
                "transfer",
                [self.env.current_contract_address(), seller, asset_amount]
            )
            # Refund highest bidder if any bid was registered below reserve (though place_bid restricts this,
            # this acts as a robust recovery logic)
            if highest_bid > U128(0):
                self.env.invoke_contract(
                    collateral_token,
                    "transfer",
                    [self.env.current_contract_address(), highest_bidder, highest_bid]
                )

            self.env.emit_event("auction_finalized", {
                "winner": Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"),
                "payout": U128(0),
                "reserve_met": False,
            })

    @external
    def refund_override(self, caller: Address, target_bidder: Address) -> U128:
        """Emergency override to manually claim failed refunds. Only admin.

        Args:
            caller: Admin address.
            target_bidder: Address to refund.
        """
        self._require_initialized()
        caller.require_auth()

        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

        pending = self.storage.get(("pending_refund", target_bidder), U128(0))
        if pending == U128(0):
            raise ContractError.ZERO_AMOUNT

        self.storage.set(("pending_refund", target_bidder), U128(0))
        collateral_token = self.storage.get("collateral_token")

        self.env.invoke_contract(
            collateral_token,
            "transfer",
            [self.env.current_contract_address(), target_bidder, pending]
        )

        self.env.emit_event("refund_override_executed", {
            "bidder": target_bidder,
            "amount": pending,
        })

        return pending

    @view
    def get_auction_status(self) -> Map:
        """Get status of the English auction."""
        res = Map()
        res.set("highest_bid", self.storage.get("highest_bid"))
        res.set("highest_bidder", self.storage.get("highest_bidder"))
        res.set("end_time", self.storage.get("end_time"))
        res.set("finalized", self.storage.get("finalized"))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
