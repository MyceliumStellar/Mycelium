"""
Dutch Auction — Price decay intervals, decay curves, purchase options, bid validation.

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
    INSUFFICIENT_PAYMENT = 6
    AUCTION_ACTIVE = 7
    INSUFFICIENT_BALANCE = 8
    ZERO_AMOUNT = 9


@contract
class DutchAuction:
    """A contract for executing a price-decaying Dutch auction for a tokenized asset."""

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
        start_price: U128,
        floor_price: U128,
        start_time: U64,
        duration: U64,
        decay_interval: U64,
    ):
        """Initialize the Dutch auction parameters.

        Args:
            admin: Admin address.
            seller: Seller address.
            asset_token: Auctioned asset token address.
            asset_amount: Amount of asset tokens.
            collateral_token: Payment token address.
            start_price: Opening high price.
            floor_price: Minimum floor price.
            start_time: Start timestamp.
            duration: Auction lifetime in seconds.
            decay_interval: Price decay step duration in seconds.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if start_price <= floor_price:
            raise ContractError.INSUFFICIENT_PAYMENT

        self.storage.set("admin", admin)
        self.storage.set("seller", seller)
        self.storage.set("asset_token", asset_token)
        self.storage.set("asset_amount", asset_amount)
        self.storage.set("collateral_token", collateral_token)
        self.storage.set("start_price", start_price)
        self.storage.set("floor_price", floor_price)
        self.storage.set("start_time", start_time)
        self.storage.set("duration", duration)
        self.storage.set("decay_interval", decay_interval)

        self.storage.set("finalized", False)
        self.storage.set("winner", Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"))
        self.storage.set("purchase_price", U128(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "seller": seller,
            "start_price": start_price,
            "floor_price": floor_price,
            "end_time": start_time + duration,
        })

    @external
    def buy(self, buyer: Address, max_spend: U128) -> U128:
        """Buy the asset immediately at the current decaying price.

        Args:
            buyer: Buyer address.
            max_spend: Maximum payment tokens buyer is willing to spend.
        """
        self._require_initialized()
        buyer.require_auth()

        if self.storage.get("finalized", False):
            raise ContractError.AUCTION_ENDED

        now = self.env.ledger().timestamp()
        start = self.storage.get("start_time")
        duration = self.storage.get("duration")

        if now < start:
            raise ContractError.AUCTION_NOT_STARTED
        if now >= start + duration:
            raise ContractError.AUCTION_ENDED

        current_price = self._calculate_current_price(now)
        if max_spend < current_price:
            raise ContractError.INSUFFICIENT_PAYMENT

        self.storage.set("finalized", True)
        self.storage.set("winner", buyer)
        self.storage.set("purchase_price", current_price)

        token = self.storage.get("collateral_token")
        seller = self.storage.get("seller")

        # Transfer payment from buyer to seller
        success = self.env.invoke_contract(
            token,
            "transfer",
            [buyer, seller, current_price]
        )
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        # Transfer asset to buyer
        asset_token = self.storage.get("asset_token")
        asset_amount = self.storage.get("asset_amount")
        self.env.invoke_contract(
            asset_token,
            "transfer",
            [self.env.current_contract_address(), buyer, asset_amount]
        )

        self.env.emit_event("asset_purchased", {
            "buyer": buyer,
            "price": current_price,
        })

        return current_price

    @external
    def claim_unsold_asset(self, caller: Address):
        """Reclaim the asset if the auction expired without any buyer. Only seller.

        Args:
            caller: Seller address.
        """
        self._require_initialized()
        caller.require_auth()

        seller = self.storage.get("seller")
        if caller != seller:
            raise ContractError.UNAUTHORIZED

        if self.storage.get("finalized", False):
            raise ContractError.AUCTION_ENDED

        now = self.env.ledger().timestamp()
        start = self.storage.get("start_time")
        duration = self.storage.get("duration")

        if now < start + duration:
            raise ContractError.AUCTION_ACTIVE

        self.storage.set("finalized", True)

        asset_token = self.storage.get("asset_token")
        asset_amount = self.storage.get("asset_amount")

        self.env.invoke_contract(
            asset_token,
            "transfer",
            [self.env.current_contract_address(), seller, asset_amount]
        )

        self.env.emit_event("unsold_reclaimed", {
            "seller": seller,
            "amount": asset_amount,
        })

    @view
    def get_current_price(self) -> U128:
        """Get the current price based on the linear decay curve and decay intervals."""
        now = self.env.ledger().timestamp()
        return self._calculate_current_price(now)

    @view
    def get_auction_status(self) -> Map:
        """Get details of the auction."""
        res = Map()
        res.set("finalized", self.storage.get("finalized"))
        res.set("winner", self.storage.get("winner"))
        res.set("purchase_price", self.storage.get("purchase_price"))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _calculate_current_price(self, now: U64) -> U128:
        start = self.storage.get("start_time")
        duration = self.storage.get("duration")

        if now < start:
            return self.storage.get("start_price")
        if now >= start + duration:
            return self.storage.get("floor_price")

        elapsed = now - start
        interval = self.storage.get("decay_interval")

        # Step-decay: round elapsed time down to nearest decay interval
        stepped_elapsed = (elapsed / interval) * interval

        start_price = self.storage.get("start_price")
        floor_price = self.storage.get("floor_price")

        # Linear decay math: price = start - (start - floor) * stepped_elapsed / duration
        decay_range = start_price - floor_price
        decayed_val = (decay_range * U128(stepped_elapsed)) / U128(duration)

        return start_price - decayed_val
