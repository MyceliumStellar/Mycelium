"""
Gradual Dutch Auction — GDA schedules, target intervals, discount bounds, decay speed options.

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
    AUCTION_EXPIRED = 5
    INSUFFICIENT_PAYMENT = 6
    SUPPLY_EXCEEDED = 7
    INSUFFICIENT_BALANCE = 8
    ZERO_AMOUNT = 9
    SLIPPAGE_EXCEEDED = 10


@contract
class GradualDutchAuction:
    """A Gradual Dutch Auction (GDA) contract for selling fungible tokens or assets continuously."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        seller: Address,
        asset_token: Address,
        asset_supply: U128,
        payment_token: Address,
        initial_price: U128,
        floor_price: U128,
        decay_constant: U64,    # Lambda parameter scaled by 1,000,000
        price_multiplier: U64,  # Scaling factor on buy, e.g. 1100000 = 1.1x (scaled by 1,000,000)
        target_interval: U64,   # Target time between sales in seconds
        start_time: U64,
    ):
        """Initialize the GDA parameters.

        Args:
            admin: Admin address.
            seller: Seller/Issuer address.
            asset_token: Token being sold.
            asset_supply: Total supply limit of assets to sell.
            payment_token: Payment token.
            initial_price: Starting price of the first item.
            floor_price: Minimum acceptable price.
            decay_constant: Decay speed coefficient (scaled by 1,000,000).
            price_multiplier: Price increase coefficient per purchase (scaled by 1,000,000).
            target_interval: Target time interval between sales.
            start_time: Start timestamp of the GDA.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if initial_price <= floor_price:
            raise ContractError.INSUFFICIENT_PAYMENT

        self.storage.set("admin", admin)
        self.storage.set("seller", seller)
        self.storage.set("asset_token", asset_token)
        self.storage.set("asset_supply", asset_supply)
        self.storage.set("payment_token", payment_token)
        self.storage.set("floor_price", floor_price)
        self.storage.set("decay_constant", decay_constant)
        self.storage.set("price_multiplier", price_multiplier)
        self.storage.set("target_interval", target_interval)
        self.storage.set("start_time", start_time)

        self.storage.set("sold_amount", U128(0))
        self.storage.set("last_sale_time", start_time)
        self.storage.set("current_base_price", initial_price)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "seller": seller,
            "asset_token": asset_token,
            "initial_price": initial_price,
            "start_time": start_time,
        })

    @external
    def buy(self, buyer: Address, quantity: U128, max_price_limit: U128) -> U128:
        """Purchase a specified quantity of assets, dynamically computing the GDA price.

        Args:
            buyer: Buyer address.
            quantity: Amount of asset tokens to buy.
            max_price_limit: Max unit price acceptable for slippage protection.
        """
        self._require_initialized()
        buyer.require_auth()

        if quantity == U128(0):
            raise ContractError.ZERO_AMOUNT

        now = self.env.ledger().timestamp()
        start = self.storage.get("start_time")
        if now < start:
            raise ContractError.AUCTION_NOT_STARTED

        sold = self.storage.get("sold_amount")
        supply = self.storage.get("asset_supply")
        if sold + quantity > supply:
            raise ContractError.SUPPLY_EXCEEDED

        # Calculate decay based on time since last sale
        last_sale = self.storage.get("last_sale_time")
        base_price = self.storage.get("current_base_price")
        decay_constant = self.storage.get("decay_constant")
        floor_price = self.storage.get("floor_price")

        elapsed = U64(0)
        if now > last_sale:
            elapsed = now - last_sale

        # Rational approximation of exponential decay: P = P_base / (1 + lambda * elapsed)
        # lambda is scaled by 1,000,000, elapsed is in seconds
        decay_denominator = U64(1000000) + (decay_constant * elapsed)
        decayed_base_price = (base_price * U128(1000000)) / U128(decay_denominator)

        if decayed_base_price < floor_price:
            decayed_base_price = floor_price

        # Calculate price for each item in the batch purchase
        multiplier = self.storage.get("price_multiplier")
        total_cost = U128(0)
        temp_price = decayed_base_price

        # We iterate to calculate GDA schedule price increments
        for _ in range(int(quantity)):
            total_cost = total_cost + temp_price
            # Increase price for next item: temp_price = temp_price * multiplier / 1000000
            temp_price = (temp_price * U128(multiplier)) / U128(1000000)

        # Slippage check
        avg_price = total_cost / quantity
        if avg_price > max_price_limit:
            raise ContractError.SLIPPAGE_EXCEEDED

        # Transfer funds from buyer to seller
        payment_token = self.storage.get("payment_token")
        seller = self.storage.get("seller")
        success = self.env.invoke_contract(
            payment_token,
            "transfer",
            [buyer, seller, total_cost]
        )
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        # Transfer asset tokens from contract to buyer
        asset_token = self.storage.get("asset_token")
        self.env.invoke_contract(
            asset_token,
            "transfer",
            [self.env.current_contract_address(), buyer, quantity]
        )

        # Update GDA state
        self.storage.set("sold_amount", sold + quantity)
        self.storage.set("last_sale_time", now)
        self.storage.set("current_base_price", temp_price)

        self.env.emit_event("purchased", {
            "buyer": buyer,
            "quantity": quantity,
            "total_cost": total_cost,
            "next_base_price": temp_price,
        })

        return total_cost

    @external
    def change_decay_speed(self, admin: Address, new_decay_constant: U64) -> Bool:
        """Update the decay speed parameter (lambda). Only admin.

        Args:
            admin: Admin address.
            new_decay_constant: The new lambda factor scaled by 1,000,000.
        """
        self._require_initialized()
        admin.require_auth()

        expected_admin = self.storage.get("admin")
        if admin != expected_admin:
            raise ContractError.UNAUTHORIZED

        self.storage.set("decay_constant", new_decay_constant)
        self.env.emit_event("decay_updated", {"decay_constant": new_decay_constant})

        return True

    @view
    def get_quote(self, quantity: U128) -> U128:
        """Get quote of the current total price for a given quantity."""
        if quantity == U128(0):
            return U128(0)

        now = self.env.ledger().timestamp()
        last_sale = self.storage.get("last_sale_time")
        base_price = self.storage.get("current_base_price")
        decay_constant = self.storage.get("decay_constant")
        floor_price = self.storage.get("floor_price")

        elapsed = U64(0)
        if now > last_sale:
            elapsed = now - last_sale

        decay_denominator = U64(1000000) + (decay_constant * elapsed)
        decayed_base_price = (base_price * U128(1000000)) / U128(decay_denominator)

        if decayed_base_price < floor_price:
            decayed_base_price = floor_price

        multiplier = self.storage.get("price_multiplier")
        total_cost = U128(0)
        temp_price = decayed_base_price

        for _ in range(int(quantity)):
            total_cost = total_cost + temp_price
            temp_price = (temp_price * U128(multiplier)) / U128(1000000)

        return total_cost

    @view
    def get_status(self) -> Map:
        """Get the current GDA state status details."""
        res = Map()
        res.set("sold_amount", self.storage.get("sold_amount"))
        res.set("current_base_price", self.storage.get("current_base_price"))
        res.set("last_sale_time", self.storage.get("last_sale_time"))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
