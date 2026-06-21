"""
Batch Auction — Uniform price calculations, supply limits, clearing price calculations, settlements.

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
    AUCTION_NOT_ENDED = 6
    ALREADY_SETTLED = 7
    INSUFFICIENT_BALANCE = 8
    ZERO_AMOUNT = 9
    BID_NOT_FOUND = 10
    REFUND_FAILED = 11


@contract
class BatchAuction:
    """A uniform-price batch auction contract where all winning bids clear at a single price."""

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
        start_time: U64,
        duration: U64,
        min_clear_price: U128,
    ):
        """Initialize the batch auction.

        Args:
            admin: Admin address.
            seller: Seller address.
            asset_token: Token to be sold.
            asset_amount: Total supply limit of asset to sell.
            collateral_token: Bidding token.
            start_time: Timestamp when bidding begins.
            duration: Duration of the bidding phase in seconds.
            min_clear_price: Minimum clearing price acceptable to the seller.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("seller", seller)
        self.storage.set("asset_token", asset_token)
        self.storage.set("asset_amount", asset_amount)
        self.storage.set("collateral_token", collateral_token)
        self.storage.set("start_time", start_time)
        self.storage.set("end_time", start_time + duration)
        self.storage.set("min_clear_price", min_clear_price)

        self.storage.set("clearing_price", U128(0))
        self.storage.set("settled", False)
        self.storage.set("bidders", Vec())
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "seller": seller,
            "asset_token": asset_token,
            "asset_amount": asset_amount,
            "end_time": start_time + duration,
        })

    @external
    def place_bid(self, bidder: Address, max_price: U128, quantity: U128) -> Bool:
        """Place a sealed bid specifying max price per unit and desired quantity.

        Args:
            bidder: Bidder address.
            max_price: Maximum price per unit the bidder is willing to pay.
            quantity: The number of units desired.
        """
        self._require_initialized()
        bidder.require_auth()

        now = self.env.ledger().timestamp()
        if now < self.storage.get("start_time"):
            raise ContractError.AUCTION_NOT_STARTED
        if now >= self.storage.get("end_time"):
            raise ContractError.AUCTION_ENDED

        if max_price == U128(0) or quantity == U128(0):
            raise ContractError.ZERO_AMOUNT

        total_escrow = max_price * quantity

        collateral_token = self.storage.get("collateral_token")
        success = self.env.invoke_contract(
            collateral_token,
            "transfer",
            [bidder, self.env.current_contract_address(), total_escrow]
        )
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        # Record or update bid
        bidders = self.storage.get("bidders")
        has_bid = False
        for i in range(len(bidders)):
            if bidders.get(i) == bidder:
                has_bid = True
                break

        if not has_bid:
            bidders.append(bidder)
            self.storage.set("bidders", bidders)

        # Refund previous escrow if update is done
        prev_price = self.storage.get(("bid_price", bidder), U128(0))
        prev_qty = self.storage.get(("bid_qty", bidder), U128(0))
        if prev_qty > U128(0):
            prev_escrow = prev_price * prev_qty
            self.env.invoke_contract(
                collateral_token,
                "transfer",
                [self.env.current_contract_address(), bidder, prev_escrow]
            )

        self.storage.set(("bid_price", bidder), max_price)
        self.storage.set(("bid_qty", bidder), quantity)

        self.env.emit_event("bid_placed", {
            "bidder": bidder,
            "max_price": max_price,
            "quantity": quantity,
        })

        return True

    @external
    def cancel_bid(self, bidder: Address) -> Bool:
        """Cancel a bid and get refunded. Only allowed during bidding phase.

        Args:
            bidder: Bidder address.
        """
        self._require_initialized()
        bidder.require_auth()

        now = self.env.ledger().timestamp()
        if now >= self.storage.get("end_time"):
            raise ContractError.AUCTION_ENDED

        prev_price = self.storage.get(("bid_price", bidder), U128(0))
        prev_qty = self.storage.get(("bid_qty", bidder), U128(0))
        if prev_qty == U128(0):
            raise ContractError.BID_NOT_FOUND

        # Reset bid state
        self.storage.set(("bid_price", bidder), U128(0))
        self.storage.set(("bid_qty", bidder), U128(0))

        # Transfer refund
        collateral_token = self.storage.get("collateral_token")
        total_escrow = prev_price * prev_qty
        self.env.invoke_contract(
            collateral_token,
            "transfer",
            [self.env.current_contract_address(), bidder, total_escrow]
        )

        self.env.emit_event("bid_cancelled", {
            "bidder": bidder,
            "amount": total_escrow,
        })

        return True

    @external
    def settle(self, caller: Address) -> U128:
        """Settle the auction, calculate uniform clearing price, and transfer assets.

        Args:
            caller: Settlement triggerer.
        """
        self._require_initialized()
        caller.require_auth()

        now = self.env.ledger().timestamp()
        if now < self.storage.get("end_time"):
            raise ContractError.AUCTION_NOT_ENDED

        if self.storage.get("settled", False):
            raise ContractError.ALREADY_SETTLED

        self.storage.set("settled", True)

        bidders = self.storage.get("bidders")
        num_bidders = len(bidders)

        if num_bidders == 0:
            # Reclaim asset to seller if no bids
            seller = self.storage.get("seller")
            asset_token = self.storage.get("asset_token")
            asset_amount = self.storage.get("asset_amount")
            self.env.invoke_contract(
                asset_token,
                "transfer",
                [self.env.current_contract_address(), seller, asset_amount]
            )
            self.env.emit_event("settled", {
                "clearing_price": U128(0),
                "total_sold": U128(0),
            })
            return U128(0)

        # Bubble sort bids by price descending to keep logic pure python and self-contained
        # In actual deployment, size would be small or sorted off-chain
        prices_list = []
        bidders_list = []
        quantities_list = []

        for i in range(num_bidders):
            b = bidders.get(i)
            p = self.storage.get(("bid_price", b), U128(0))
            q = self.storage.get(("bid_qty", b), U128(0))
            if q > U128(0):
                prices_list.append(p)
                bidders_list.append(b)
                quantities_list.append(q)

        n = len(prices_list)
        for i in range(n):
            for j in range(0, n - i - 1):
                if prices_list[j] < prices_list[j + 1]:
                    # Swap
                    prices_list[j], prices_list[j + 1] = prices_list[j + 1], prices_list[j]
                    bidders_list[j], bidders_list[j + 1] = bidders_list[j + 1], bidders_list[j]
                    quantities_list[j], quantities_list[j + 1] = quantities_list[j + 1], quantities_list[j]

        # Calculate clearing price
        supply = self.storage.get("asset_amount")
        min_clear_price = self.storage.get("min_clear_price")
        clearing_price = min_clear_price
        total_demand = U128(0)
        cutoff_index = 0
        clearing_found = False

        for i in range(n):
            p = prices_list[i]
            q = quantities_list[i]

            if p < min_clear_price:
                break

            if total_demand + q >= supply:
                clearing_price = p
                cutoff_index = i
                clearing_found = True
                break
            else:
                total_demand = total_demand + q
                clearing_price = p
                cutoff_index = i

        if not clearing_found and n > 0:
            # Supply exceeds total demand; check if last valid price meets min
            if prices_list[n - 1] >= min_clear_price:
                clearing_price = prices_list[n - 1]
                cutoff_index = n - 1

        self.storage.set("clearing_price", clearing_price)

        # Distribute filled amounts & refunds
        seller = self.storage.get("seller")
        asset_token = self.storage.get("asset_token")
        collateral_token = self.storage.get("collateral_token")
        total_sold = U128(0)
        total_payout = U128(0)

        for i in range(n):
            b = bidders_list[i]
            p = prices_list[i]
            q = quantities_list[i]
            locked_funds = p * q

            if p > clearing_price:
                # Fully filled
                filled_qty = q
                refund_amt = locked_funds - (clearing_price * filled_qty)
            elif p == clearing_price:
                # Marginally filled: fill up to remaining supply
                remaining = supply - total_sold
                if remaining >= q:
                    filled_qty = q
                else:
                    filled_qty = remaining
                refund_amt = locked_funds - (clearing_price * filled_qty)
            else:
                # Not filled
                filled_qty = U128(0)
                refund_amt = locked_funds

            total_sold = total_sold + filled_qty
            total_payout = total_payout + (clearing_price * filled_qty)

            # Send fill asset
            if filled_qty > U128(0):
                self.env.invoke_contract(
                    asset_token,
                    "transfer",
                    [self.env.current_contract_address(), b, filled_qty]
                )

            # Send refund
            if refund_amt > U128(0):
                self.env.invoke_contract(
                    collateral_token,
                    "transfer",
                    [self.env.current_contract_address(), b, refund_amt]
                )

        # Pay seller
        if total_payout > U128(0):
            self.env.invoke_contract(
                collateral_token,
                "transfer",
                [self.env.current_contract_address(), seller, total_payout]
            )

        # Return remaining unsold assets to seller
        if total_sold < supply:
            unsold = supply - total_sold
            self.env.invoke_contract(
                asset_token,
                "transfer",
                [self.env.current_contract_address(), seller, unsold]
            )

        self.env.emit_event("settled", {
            "clearing_price": clearing_price,
            "total_sold": total_sold,
            "total_payout": total_payout,
        })

        return clearing_price

    @view
    def get_bid(self, bidder: Address) -> Map:
        """Get bid details for a bidder."""
        res = Map()
        res.set("price", self.storage.get(("bid_price", bidder), U128(0)))
        res.set("quantity", self.storage.get(("bid_qty", bidder), U128(0)))
        return res

    @view
    def get_status(self) -> Map:
        """Get general status of the auction."""
        res = Map()
        res.set("settled", self.storage.get("settled", False))
        res.set("clearing_price", self.storage.get("clearing_price", U128(0)))
        res.set("end_time", self.storage.get("end_time", U64(0)))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
