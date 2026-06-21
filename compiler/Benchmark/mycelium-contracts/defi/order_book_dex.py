"""
Order Book DEX — On-chain central limit order book (CLOB).

Features:
  - Base and Quote token pair custody and settlement
  - Limit orders and Market orders (Buy/Sell)
  - Price-time priority matching algorithm
  - Partial fills and residual order book placement
  - Safe token custody with escrow locking during placement
  - Order cancellation and refund of remaining size
  - Self-Trade Prevention (STP) - Cancels maker order if matched with same creator
  - Tiered maker/taker fee structure based on cumulative volume per user
  - Dynamic order book queries for depth analysis

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
    REENTRANCY_GUARD = 4
    ZERO_AMOUNT = 5
    ZERO_PRICE = 6
    ORDER_NOT_FOUND = 7
    INSUFFICIENT_FUNDS = 8
    SELF_TRADE_PREVENTION = 9
    OVERFLOW = 10


# Constants
PRICE_PRECISION = U128(1_000_000)  # Price scaled to 6 decimals
FEE_DENOMINATOR = U128(10000)     # Fee in basis points (10000 = 100%)


@contract
class OrderBookDEX:
    """
    On-chain Central Limit Order Book DEX with matching engine, escrow,
    STP, and volume-based tiered fee structure.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ------------------------------------------------------------------ #
    #  Initialization
    # ------------------------------------------------------------------ #

    @external
    def initialize(self, admin: Address, token_base: Address, token_quote: Address):
        """Set up trading pair and administration."""
        admin.require_auth()

        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("token_base", token_base)
        self.storage.set("token_quote", token_quote)
        self.storage.set("next_order_id", U64(1))
        
        # Order queues (Bids and Asks IDs sorted list)
        self.storage.set("bid_ids", Vec())
        self.storage.set("ask_ids", Vec())
        
        self.storage.set("reentrancy_locked", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "token_base": token_base,
            "token_quote": token_quote
        })

    # ------------------------------------------------------------------ #
    #  Order Actions
    # ------------------------------------------------------------------ #

    @external
    def place_limit_order(
        self,
        caller: Address,
        is_buy: Bool,
        price: U128,
        amount: U128,
    ) -> U64:
        """
        Place a limit order. Locks tokens in escrow and matches against the order book.
        Unfilled portions are posted to the book in price-time priority.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_locked()
        self._set_locked(True)

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT
        if price == U128(0):
            raise ContractError.ZERO_PRICE

        token_base = self.storage.get("token_base")
        token_quote = self.storage.get("token_quote")

        # Escrow Lock
        if is_buy:
            # Lock Quote tokens: price * amount // precision
            lock_amount = (amount * price) // PRICE_PRECISION
            if lock_amount == U128(0):
                raise ContractError.ZERO_AMOUNT
            self.env.transfer(caller, self.env.current_contract(), token_quote, lock_amount)
        else:
            # Lock Base tokens
            self.env.transfer(caller, self.env.current_contract(), token_base, amount)

        order_id = self.storage.get("next_order_id")
        self.storage.set("next_order_id", order_id + U64(1))

        # Create Order dictionary
        order = {
            "id": order_id,
            "creator": caller,
            "is_buy": is_buy,
            "price": price,
            "amount": amount,
            "original_amount": amount,
            "timestamp": self.env.ledger().timestamp(),
        }

        # Perform matching
        filled_qty, quote_filled = self._match_order(order)
        
        # Adjust order size
        order["amount"] = order["amount"] - filled_qty

        # If order is fully filled, we don't save it to order book queues
        if order["amount"] > U128(0):
            # Save order to book
            self.storage.set(f"order:{order_id}", order)
            self._insert_into_book(order_id, is_buy, price)
        else:
            # If buy order is fully filled, any excess quote tokens from lock (due to better match price) are refunded
            if is_buy:
                excess_quote = ((amount * price) // PRICE_PRECISION) - quote_filled
                if excess_quote > U128(0):
                    self.env.transfer(self.env.current_contract(), caller, token_quote, excess_quote)

        self._set_locked(False)

        self.env.emit_event("order_placed", {
            "order_id": order_id,
            "creator": caller,
            "is_buy": is_buy,
            "price": price,
            "amount": amount,
            "filled": filled_qty
        })
        return order_id

    @external
    def place_market_order(
        self,
        caller: Address,
        is_buy: Bool,
        amount: U128,
    ) -> U128:
        """
        Place a market order. Instantly matches against resting orders.
        Does not post to the book. Leftover amounts are cancelled.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_locked()
        self._set_locked(True)

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        token_base = self.storage.get("token_base")
        token_quote = self.storage.get("token_quote")

        # For market buy, we lock a temporary max quote balance from user.
        # Alternatively, we transfer dynamically during matching.
        # Let's perform match queries first to find out exactly how much quote we need,
        # or lock quote assuming a reasonable depth, or pull quote token from user during fill.
        # In Stellar/Soroban require_auth, pulling dynamically is valid. Let's do that!
        
        # Set up a virtual order
        market_order = {
            "id": U64(0),
            "creator": caller,
            "is_buy": is_buy,
            "price": U128(0), # 0 indicates market price matching
            "amount": amount,
            "original_amount": amount,
            "timestamp": self.env.ledger().timestamp(),
        }

        filled_qty, quote_filled = self._match_order(market_order)

        self._set_locked(False)

        self.env.emit_event("market_order_executed", {
            "caller": caller,
            "is_buy": is_buy,
            "requested_qty": amount,
            "filled_qty": filled_qty,
            "quote_filled": quote_filled
        })
        return filled_qty

    @external
    def cancel_order(self, caller: Address, order_id: U64):
        """Cancel a resting order and refund the remaining locked amount."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_locked()
        self._set_locked(True)

        order_key = f"order:{order_id}"
        order = self.storage.get(order_key, None)
        if order is None:
            raise ContractError.ORDER_NOT_FOUND

        if order["creator"] != caller:
            raise ContractError.UNAUTHORIZED

        is_buy = order["is_buy"]
        remaining_amt = order["amount"]

        token_base = self.storage.get("token_base")
        token_quote = self.storage.get("token_quote")

        # Refund locked amount
        if is_buy:
            refund_quote = (remaining_amt * order["price"]) // PRICE_PRECISION
            if refund_quote > U128(0):
                self.env.transfer(self.env.current_contract(), caller, token_quote, refund_quote)
        else:
            if remaining_amt > U128(0):
                self.env.transfer(self.env.current_contract(), caller, token_base, remaining_amt)

        # Remove from sorted queue lists
        self._remove_from_book(order_id, is_buy)
        self.storage.remove(order_key)

        self._set_locked(False)

        self.env.emit_event("order_cancelled", {
            "order_id": order_id,
            "creator": caller,
            "refunded_amount": remaining_amt
        })

    # ------------------------------------------------------------------ #
    #  Matching Engine Implementation
    # ------------------------------------------------------------------ #

    def _match_order(self, taker_order: Map) -> tuple:
        """
        Iterates the opposing order queue and performs trade matching.
        Handles partial fills, fee settlements, volume calculations, and STP.
        Returns: (filled_qty, quote_filled)
        """
        is_buy = taker_order["is_buy"]
        taker_price = taker_order["price"]
        taker_creator = taker_order["creator"]

        token_base = self.storage.get("token_base")
        token_quote = self.storage.get("token_quote")

        # Select book side to match against
        book_ids = self.storage.get("ask_ids") if is_buy else self.storage.get("bid_ids")

        filled_qty = U128(0)
        quote_filled = U128(0)
        
        # Index tracking for removal of filled maker orders
        makers_to_remove = Vec()
        
        # Copy to avoid mutation issues during iteration
        for i in range(len(book_ids)):
            maker_id = book_ids[i]
            maker = self.storage.get(f"order:{maker_id}", None)
            if maker is None:
                continue

            # Price limit check (only if taker is limit order)
            if taker_price > U128(0):
                if is_buy and maker["price"] > taker_price:
                    break  # Maker ask is too high
                if not is_buy and maker["price"] < taker_price:
                    break  # Maker bid is too low

            # Self-Trade Prevention (STP) check
            if maker["creator"] == taker_creator:
                # Cancel maker order and continue
                makers_to_remove.append(maker_id)
                self._refund_maker_order(maker)
                self.env.emit_event("self_trade_prevented", {
                    "maker_order_id": maker_id,
                    "taker_creator": taker_creator
                })
                continue

            # Match quantity
            remaining_taker = taker_order["amount"] - filled_qty
            match_qty = min(remaining_taker, maker["amount"])
            
            match_price = maker["price"]
            match_quote_value = (match_qty * match_price) // PRICE_PRECISION

            # Settle Fees
            maker_fee_bps, taker_fee_bps = self._get_fee_tiers(maker["creator"], taker_creator)
            
            maker_fee_quote = (match_quote_value * maker_fee_bps) // FEE_DENOMINATOR
            taker_fee_base = (match_qty * taker_fee_bps) // FEE_DENOMINATOR

            # Settle transfers
            if is_buy:
                # Taker buys base: taker receives base - fee
                # Taker pays quote value to contract (taken from escrow if limit, or direct transfer if market)
                if taker_order["id"] == U64(0):
                    # Market buy: pull quote directly
                    self.env.transfer(taker_creator, self.env.current_contract(), token_quote, match_quote_value)
                
                # Pay maker their quote - maker_fee
                maker_net_quote = match_quote_value - maker_fee_quote
                self.env.transfer(self.env.current_contract(), maker["creator"], token_quote, maker_net_quote)
                
                # Pay taker their base - taker_fee
                taker_net_base = match_qty - taker_fee_base
                self.env.transfer(self.env.current_contract(), taker_creator, token_base, taker_net_base)
            else:
                # Taker sells base: taker pays base to contract (escrowed if limit, direct if market)
                if taker_order["id"] == U64(0):
                    # Market sell: pull base directly
                    self.env.transfer(taker_creator, self.env.current_contract(), token_base, match_qty)

                # Pay taker their quote - taker fee (quote)
                taker_fee_quote = (match_quote_value * taker_fee_bps) // FEE_DENOMINATOR
                taker_net_quote = match_quote_value - taker_fee_quote
                self.env.transfer(self.env.current_contract(), taker_creator, token_quote, taker_net_quote)

                # Pay maker their base - maker fee (base)
                maker_fee_base = (match_qty * maker_fee_bps) // FEE_DENOMINATOR
                maker_net_base = match_qty - maker_fee_base
                self.env.transfer(self.env.current_contract(), maker["creator"], token_base, maker_net_base)

            # Update volume stats for fee tiers
            self._update_user_volume(maker["creator"], match_quote_value)
            self._update_user_volume(taker_creator, match_quote_value)

            # Update book amounts
            maker["amount"] = maker["amount"] - match_qty
            filled_qty = filled_qty + match_qty
            quote_filled = quote_filled + match_quote_value

            if maker["amount"] == U128(0):
                makers_to_remove.append(maker_id)
                self.storage.remove(f"order:{maker_id}")
            else:
                self.storage.set(f"order:{maker_id}", maker)

            self.env.emit_event("trade_matched", {
                "maker_order_id": maker_id,
                "maker": maker["creator"],
                "taker": taker_creator,
                "is_buy": is_buy,
                "qty": match_qty,
                "price": match_price,
                "quote_value": match_quote_value
            })

            # Check if taker is filled
            if filled_qty == taker_order["amount"]:
                break

        # Remove finished makers from sorted queue
        for r_id in makers_to_remove:
            self._remove_from_book(r_id, not is_buy)

        return (filled_qty, quote_filled)

    # ------------------------------------------------------------------ #
    #  Order Book Management Helpers
    # ------------------------------------------------------------------ #

    def _insert_into_book(self, order_id: U64, is_buy: Bool, price: U128):
        """Inserts an order ID into the bid/ask vector preserving price-time order."""
        ids_key = "bid_ids" if is_buy else "ask_ids"
        ids = self.storage.get(ids_key)

        inserted = False
        new_ids = Vec()

        for i in range(len(ids)):
            curr_id = ids[i]
            curr_order = self.storage.get(f"order:{curr_id}")
            if curr_order is None:
                continue

            # Bid side sorting: price descending, then timestamp/id ascending
            # Ask side sorting: price ascending, then timestamp/id ascending
            should_insert_before = False
            if is_buy:
                if price > curr_order["price"]:
                    should_insert_before = True
            else:
                if price < curr_order["price"]:
                    should_insert_before = True

            if should_insert_before and not inserted:
                new_ids.append(order_id)
                inserted = True

            new_ids.append(curr_id)

        if not inserted:
            new_ids.append(order_id)

        self.storage.set(ids_key, new_ids)

    def _remove_from_book(self, order_id: U64, is_buy: Bool):
        """Removes order_id from queue vector."""
        ids_key = "bid_ids" if is_buy else "ask_ids"
        ids = self.storage.get(ids_key)
        new_ids = Vec()
        for i in range(len(ids)):
            if ids[i] != order_id:
                new_ids.append(ids[i])
        self.storage.set(ids_key, new_ids)

    def _refund_maker_order(self, maker: Map):
        """Refunds remainder of maker order (called when STP or cancel fires)."""
        token_base = self.storage.get("token_base")
        token_quote = self.storage.get("token_quote")

        if maker["is_buy"]:
            refund = (maker["amount"] * maker["price"]) // PRICE_PRECISION
            self.env.transfer(self.env.current_contract(), maker["creator"], token_quote, refund)
        else:
            self.env.transfer(self.env.current_contract(), maker["creator"], token_base, maker["amount"])
            
        self._remove_from_book(maker["id"], maker["is_buy"])
        self.storage.remove(f"order:{maker['id']}")

    # ------------------------------------------------------------------ #
    #  Volume and Fee Tiers
    # ------------------------------------------------------------------ #

    def _update_user_volume(self, user: Address, volume: U128):
        """Track trading volume (quote denominated) to assign fee discount levels."""
        vol_key = f"volume:{user}"
        self.storage.set(vol_key, self.storage.get(vol_key, U128(0)) + volume)

    def _get_fee_tiers(self, maker: Address, taker: Address) -> tuple:
        """
        Determine fee basis points based on accumulated trading volume:
        - Volume < 10k: Maker 10 bps, Taker 20 bps
        - Volume < 100k: Maker 5 bps, Taker 12 bps
        - Volume >= 100k: Maker 0 bps, Taker 5 bps
        """
        maker_vol = self.storage.get(f"volume:{maker}", U128(0))
        taker_vol = self.storage.get(f"volume:{taker}", U128(0))

        maker_fee = U128(10) # 0.1% default
        if maker_vol >= U128(100_000 * PRICE_PRECISION):
            maker_fee = U128(0)
        elif maker_vol >= U128(10_000 * PRICE_PRECISION):
            maker_fee = U128(5)

        taker_fee = U128(20) # 0.2% default
        if taker_vol >= U128(100_000 * PRICE_PRECISION):
            taker_fee = U128(5)
        elif taker_vol >= U128(10_000 * PRICE_PRECISION):
            taker_fee = U128(12)

        return (maker_fee, taker_fee)

    # ------------------------------------------------------------------ #
    #  View & Order Book Queries
    # ------------------------------------------------------------------ #

    @view
    def get_order(self, order_id: U64) -> Map:
        """Fetch details of an order from storage."""
        order = self.storage.get(f"order:{order_id}", None)
        if order is None:
            raise ContractError.ORDER_NOT_FOUND
        return order

    @view
    def get_order_book(self, max_depth: U64) -> Map:
        """
        Query order book bids and asks list.
        Returns: { bids: [...], asks: [...] } up to max_depth.
        """
        bid_ids = self.storage.get("bid_ids")
        ask_ids = self.storage.get("ask_ids")

        bids = Vec()
        depth = min(len(bid_ids), int(max_depth))
        for i in range(depth):
            ord_ = self.storage.get(f"order:{bid_ids[i]}")
            if ord_ is not None:
                bids.append(ord_)

        asks = Vec()
        depth = min(len(ask_ids), int(max_depth))
        for i in range(depth):
            ord_ = self.storage.get(f"order:{ask_ids[i]}")
            if ord_ is not None:
                asks.append(ord_)

        return {
            "bids": bids,
            "asks": asks
        }

    @view
    def get_user_volume(self, user: Address) -> U128:
        """Query user trading volume."""
        return self.storage.get(f"volume:{user}", U128(0))

    # ------------------------------------------------------------------ #
    #  Private Lifecycle Helpers
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_not_locked(self):
        if self.storage.get("reentrancy_locked", False):
            raise ContractError.REENTRANCY_GUARD

    def _set_locked(self, locked: Bool):
        self.storage.set("reentrancy_locked", locked)
