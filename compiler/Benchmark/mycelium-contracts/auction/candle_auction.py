"""
Candle Auction — Random end window, block hash evaluation, validation, refund controls.

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
    BID_TOO_LOW = 7
    ALREADY_FINALIZED = 8
    NOT_FINALIZED = 9
    NO_BIDS = 10
    ZERO_AMOUNT = 11
    INSUFFICIENT_BALANCE = 12


@contract
class CandleAuction:
    """A retroactive candle auction contract where the actual end time is determined randomly."""

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
        candle_start_time: U64,
        end_time: U64,
    ):
        """Initialize the Candle Auction.

        Args:
            admin: Admin address.
            seller: Seller address.
            asset_token: Token to be sold.
            asset_amount: Amount of asset tokens.
            collateral_token: Bid payment token.
            reserve_price: Minimum bid price.
            min_increment: Minimum bidding step increment.
            start_time: Auction opening timestamp.
            candle_start_time: Start of the random end window.
            end_time: Hard deadline timestamp when bidding terminates.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if candle_start_time <= start_time or end_time <= candle_start_time:
            raise ContractError.BID_TOO_LOW

        self.storage.set("admin", admin)
        self.storage.set("seller", seller)
        self.storage.set("asset_token", asset_token)
        self.storage.set("asset_amount", asset_amount)
        self.storage.set("collateral_token", collateral_token)
        self.storage.set("reserve_price", reserve_price)
        self.storage.set("min_increment", min_increment)
        self.storage.set("start_time", start_time)
        self.storage.set("candle_start_time", candle_start_time)
        self.storage.set("end_time", end_time)

        self.storage.set("highest_bid", U128(0))
        self.storage.set("bids_count", U64(0))
        self.storage.set("finalized", False)
        self.storage.set("winner", Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"))
        self.storage.set("winning_bid", U128(0))
        self.storage.set("retroactive_end_time", U64(0))

        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "seller": seller,
            "candle_start": candle_start_time,
            "end_time": end_time,
        })

    @external
    def place_bid(self, bidder: Address, amount: U128) -> Bool:
        """Place an ascending bid during the bidding phase.

        Args:
            bidder: Bidder address.
            amount: Bid amount.
        """
        self._require_initialized()
        bidder.require_auth()

        now = self.env.ledger().timestamp()
        if now < self.storage.get("start_time"):
            raise ContractError.AUCTION_NOT_STARTED
        if now >= self.storage.get("end_time"):
            raise ContractError.AUCTION_ENDED

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        highest_bid = self.storage.get("highest_bid")
        min_inc = self.storage.get("min_increment")
        reserve = self.storage.get("reserve_price")

        # Bid checking
        if highest_bid == U128(0):
            if amount < reserve:
                raise ContractError.BID_TOO_LOW
        else:
            if amount < highest_bid + min_inc:
                raise ContractError.BID_TOO_LOW

        collateral_token = self.storage.get("collateral_token")
        # Escrow full bid amount
        success = self.env.invoke_contract(
            collateral_token,
            "transfer",
            [bidder, self.env.current_contract_address(), amount]
        )
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        # Record bid in sequence
        count = self.storage.get("bids_count")
        bid_entry = Map()
        bid_entry.set("bidder", bidder)
        bid_entry.set("amount", amount)
        bid_entry.set("timestamp", now)
        self.storage.set(("bid_entry", count), bid_entry)
        self.storage.set("bids_count", count + U64(1))

        # Update highest bid
        self.storage.set("highest_bid", amount)

        # Track total deposited funds for refund calculations
        prev_dep = self.storage.get(("deposited", bidder), U128(0))
        self.storage.set(("deposited", bidder), prev_dep + amount)

        self.env.emit_event("bid_placed", {
            "bidder": bidder,
            "amount": amount,
            "timestamp": now,
        })

        return True

    @external
    def finalize(self, caller: Address, random_seed: U64) -> Address:
        """Retroactively determine the auction's end time and select the winner. Only admin.

        Args:
            caller: Admin.
            random_seed: Randomness seed to determine retroactive end time.
        """
        self._require_initialized()
        caller.require_auth()

        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

        if self.storage.get("finalized", False):
            raise ContractError.ALREADY_FINALIZED

        now = self.env.ledger().timestamp()
        if now < self.storage.get("end_time"):
            raise ContractError.AUCTION_ACTIVE

        self.storage.set("finalized", True)

        candle_start = self.storage.get("candle_start_time")
        end_time = self.storage.get("end_time")

        # Determine retroactive end timestamp
        candle_window = end_time - candle_start
        offset = random_seed % candle_window
        retroactive_end = candle_start + offset
        self.storage.set("retroactive_end_time", retroactive_end)

        bids_count = self.storage.get("bids_count")

        winner = Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF")
        winning_bid = U128(0)

        # Iterate bids sequence to find the highest bid placed before retroactive end
        # Since bids are strictly ascending, the last bid before retroactive end is the highest
        for i in range(bids_count):
            entry = self.storage.get(("bid_entry", i))
            ts = entry.get("timestamp")
            if ts <= retroactive_end:
                winner = entry.get("bidder")
                winning_bid = entry.get("amount")

        self.storage.set("winner", winner)
        self.storage.set("winning_bid", winning_bid)

        seller = self.storage.get("seller")
        asset_token = self.storage.get("asset_token")
        asset_amount = self.storage.get("asset_amount")
        collateral_token = self.storage.get("collateral_token")

        if winning_bid > U128(0):
            # Winner gets asset
            self.env.invoke_contract(
                asset_token,
                "transfer",
                [self.env.current_contract_address(), winner, asset_amount]
            )
            # Seller gets winning bid amount
            self.env.invoke_contract(
                collateral_token,
                "transfer",
                [self.env.current_contract_address(), seller, winning_bid]
            )
            self.env.emit_event("finalized", {
                "winner": winner,
                "winning_bid": winning_bid,
                "retroactive_end": retroactive_end,
            })
        else:
            # Reclaim asset to seller if no bids qualified
            self.env.invoke_contract(
                asset_token,
                "transfer",
                [self.env.current_contract_address(), seller, asset_amount]
            )
            self.env.emit_event("finalized", {
                "winner": winner,
                "winning_bid": U128(0),
                "retroactive_end": retroactive_end,
            })

        return winner

    @external
    def claim_refund(self, claimant: Address) -> U128:
        """Claim refund of losing bids after the auction has been finalized.

        Args:
            claimant: Bidder reclaiming their funds.
        """
        self._require_initialized()
        claimant.require_auth()

        if not self.storage.get("finalized", False):
            raise ContractError.NOT_FINALIZED

        deposited = self.storage.get(("deposited", claimant), U128(0))
        if deposited == U128(0):
            raise ContractError.ZERO_AMOUNT

        winner = self.storage.get("winner")
        winning_bid = self.storage.get("winning_bid")

        refund_amount = deposited
        if claimant == winner:
            if deposited >= winning_bid:
                refund_amount = deposited - winning_bid
            else:
                refund_amount = U128(0)

        if refund_amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        self.storage.set(("deposited", claimant), deposited - refund_amount)

        collateral_token = self.storage.get("collateral_token")
        self.env.invoke_contract(
            collateral_token,
            "transfer",
            [self.env.current_contract_address(), claimant, refund_amount]
        )

        self.env.emit_event("refund_claimed", {
            "claimant": claimant,
            "amount": refund_amount,
        })

        return refund_amount

    @view
    def get_status(self) -> Map:
        """Get status of candle auction."""
        res = Map()
        res.set("finalized", self.storage.get("finalized"))
        res.set("winner", self.storage.get("winner"))
        res.set("winning_bid", self.storage.get("winning_bid"))
        res.set("retroactive_end_time", self.storage.get("retroactive_end_time"))
        res.set("highest_bid", self.storage.get("highest_bid"))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
