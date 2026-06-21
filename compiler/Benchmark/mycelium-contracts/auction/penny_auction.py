"""
Penny Auction — Incremental bids, bid fee consumption, extension countdown, bid pack purchase rules.

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
    NO_BIDS_RECLAIM = 7
    INSUFFICIENT_BALANCE = 8
    ZERO_AMOUNT = 9
    PAYMENT_WINDOW_EXPIRED = 10
    ALREADY_FINALIZED = 11


@contract
class PennyAuction:
    """A penny auction contract where each bid increases the price slightly and extends the countdown."""

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
        payment_token: Address,
        bid_price_increment: U128,
        countdown_extension: U64,
        start_time: U64,
        duration: U64,
        bid_credit_price: U128,
        claim_window: U64,
    ):
        """Initialize the penny auction.

        Args:
            admin: Admin address.
            seller: Seller address.
            asset_token: Token to be sold.
            asset_amount: Amount of asset tokens.
            payment_token: Token used for bid packs and final purchase.
            bid_price_increment: Amount the purchase price increases with each bid.
            countdown_extension: Seconds to extend the end time if a bid occurs near the end.
            start_time: Bidding start timestamp.
            duration: Initial duration of the auction.
            bid_credit_price: Cost in payment_token to buy 1 bid credit.
            claim_window: Number of seconds the winner has to claim/pay for the asset after auction end.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("seller", seller)
        self.storage.set("asset_token", asset_token)
        self.storage.set("asset_amount", asset_amount)
        self.storage.set("payment_token", payment_token)
        self.storage.set("bid_price_increment", bid_price_increment)
        self.storage.set("countdown_extension", countdown_extension)
        self.storage.set("start_time", start_time)
        self.storage.set("end_time", start_time + duration)
        self.storage.set("bid_credit_price", bid_credit_price)
        self.storage.set("claim_window", claim_window)

        self.storage.set("current_price", U128(0))
        self.storage.set("highest_bidder", Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"))
        self.storage.set("finalized", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "seller": seller,
            "asset_token": asset_token,
            "end_time": start_time + duration,
        })

    @external
    def buy_bid_credits(self, buyer: Address, num_credits: U128) -> U128:
        """Purchase bid credits to be used for placing bids.

        Args:
            buyer: Buyer address.
            num_credits: Number of bid credits to buy.
        """
        self._require_initialized()
        buyer.require_auth()

        if num_credits == U128(0):
            raise ContractError.ZERO_AMOUNT

        price_per_credit = self.storage.get("bid_credit_price")
        cost = num_credits * price_per_credit

        payment_token = self.storage.get("payment_token")
        seller = self.storage.get("seller")

        # Collect fee directly to seller (or contract escrow)
        success = self.env.invoke_contract(
            payment_token,
            "transfer",
            [buyer, seller, cost]
        )
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        current_bal = self.storage.get(("credits", buyer), U128(0))
        self.storage.set(("credits", buyer), current_bal + num_credits)

        self.env.emit_event("credits_purchased", {
            "buyer": buyer,
            "amount": num_credits,
            "cost": cost,
        })

        return num_credits

    @external
    def place_bid(self, bidder: Address) -> U128:
        """Place a bid using 1 bid credit, incrementing the price and extending the countdown.

        Args:
            bidder: Bidder address.
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

        # Consume 1 bid credit
        credits = self.storage.get(("credits", bidder), U128(0))
        if credits < U128(1):
            raise ContractError.INSUFFICIENT_BALANCE
        self.storage.set(("credits", bidder), credits - U128(1))

        # Update price
        current_price = self.storage.get("current_price")
        increment = self.storage.get("bid_price_increment")
        new_price = current_price + increment
        self.storage.set("current_price", new_price)

        # Update highest bidder
        self.storage.set("highest_bidder", bidder)

        # Extend end time if near countdown expiration
        extension = self.storage.get("countdown_extension")
        if end - now < extension:
            end = now + extension
            self.storage.set("end_time", end)
            self.env.emit_event("auction_extended", {"new_end_time": end})

        self.env.emit_event("bid_placed", {
            "bidder": bidder,
            "new_price": new_price,
        })

        return new_price

    @external
    def claim_winner_asset(self, winner: Address) -> Bool:
        """Claim the asset as the winner by paying the final penny price.

        Args:
            winner: Winning bidder address.
        """
        self._require_initialized()
        winner.require_auth()

        if self.storage.get("finalized", False):
            raise ContractError.ALREADY_FINALIZED

        now = self.env.ledger().timestamp()
        end = self.storage.get("end_time")
        if now < end:
            raise ContractError.AUCTION_ACTIVE

        highest_bidder = self.storage.get("highest_bidder")
        if winner != highest_bidder:
            raise ContractError.UNAUTHORIZED

        claim_window = self.storage.get("claim_window")
        if now > end + claim_window:
            raise ContractError.PAYMENT_WINDOW_EXPIRED

        self.storage.set("finalized", True)

        current_price = self.storage.get("current_price")
        payment_token = self.storage.get("payment_token")
        seller = self.storage.get("seller")

        # Winner pays current_price to seller
        if current_price > U128(0):
            success = self.env.invoke_contract(
                payment_token,
                "transfer",
                [winner, seller, current_price]
            )
            if not success:
                raise ContractError.INSUFFICIENT_BALANCE

        # Transfer asset to winner
        asset_token = self.storage.get("asset_token")
        asset_amount = self.storage.get("asset_amount")
        self.env.invoke_contract(
            asset_token,
            "transfer",
            [self.env.current_contract_address(), winner, asset_amount]
        )

        self.env.emit_event("claimed", {
            "winner": winner,
            "price_paid": current_price,
        })

        return True

    @external
    def seller_reclaim(self, seller: Address) -> Bool:
        """Reclaim the asset if the winner fails to claim within the window or if no bids.

        Args:
            seller: Seller address.
        """
        self._require_initialized()
        seller.require_auth()

        expected_seller = self.storage.get("seller")
        if seller != expected_seller:
            raise ContractError.UNAUTHORIZED

        if self.storage.get("finalized", False):
            raise ContractError.ALREADY_FINALIZED

        now = self.env.ledger().timestamp()
        end = self.storage.get("end_time")

        if now < end:
            raise ContractError.AUCTION_ACTIVE

        highest_bidder = self.storage.get("highest_bidder")
        claim_window = self.storage.get("claim_window")

        has_bids = highest_bidder != Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF")
        if has_bids and (now <= end + claim_window):
            raise ContractError.AUCTION_ACTIVE

        self.storage.set("finalized", True)

        # Return asset to seller
        asset_token = self.storage.get("asset_token")
        asset_amount = self.storage.get("asset_amount")
        self.env.invoke_contract(
            asset_token,
            "transfer",
            [self.env.current_contract_address(), seller, asset_amount]
        )

        self.env.emit_event("reclaimed", {
            "seller": seller,
            "amount": asset_amount,
        })

        return True

    @view
    def get_credits(self, user: Address) -> U128:
        """Get the bid credit balance of a user."""
        return self.storage.get(("credits", user), U128(0))

    @view
    def get_auction_status(self) -> Map:
        """Get current status of the auction."""
        res = Map()
        res.set("highest_bidder", self.storage.get("highest_bidder"))
        res.set("current_price", self.storage.get("current_price"))
        res.set("end_time", self.storage.get("end_time"))
        res.set("finalized", self.storage.get("finalized"))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
