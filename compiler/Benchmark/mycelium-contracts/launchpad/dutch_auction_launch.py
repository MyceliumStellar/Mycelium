"""
Dutch Auction Launch — Token sale via Dutch auction, uniform clearing price discovery, refund processing of excess bids, cap management.

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
    AUCTION_NOT_ACTIVE = 4
    AUCTION_ALREADY_ENDED = 5
    PRICE_TOO_HIGH = 6
    INVALID_BID_AMOUNT = 7
    INVALID_STATE = 8
    NOTHING_TO_CLAIM = 9
    INSUFFICIENT_TOKENS = 10
    ALREADY_CLAIMED = 11
    INVALID_PARAMS = 12

class AuctionState:
    ACTIVE = 0
    SUCCESSFUL = 1
    FAILED = 2

@contract
class DutchAuctionLaunch:
    """A Dutch auction launchpad contract with uniform clearing price discovery and refund processing."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        launch_token: Address,
        reserve_token: Address,
        tokens_for_sale: U128,
        start_time: U64,
        end_time: U64,
        start_price: U128,    # Price of 1 launch token in reserve tokens (scaled by 10^6)
        end_price: U128,      # Reserve price (scaled by 10^6)
        min_funding_goal: U128,
    ):
        """Initialize the Dutch auction.

        Args:
            admin: Admin address.
            launch_token: Token being sold.
            reserve_token: Token used for purchasing.
            tokens_for_sale: Amount of launch tokens to sell.
            start_time: Auction start timestamp.
            end_time: Auction end timestamp.
            start_price: Starting token price (scaled by 1,000,000).
            end_price: Ending token price (scaled by 1,000,000).
            min_funding_goal: Minimum total reserve tokens required for success.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if start_time >= end_time or start_price <= end_price:
            raise ContractError.INVALID_PARAMS

        if tokens_for_sale == 0:
            raise ContractError.INVALID_PARAMS

        self.storage.set("admin", admin)
        self.storage.set("launch_token", launch_token)
        self.storage.set("reserve_token", reserve_token)
        self.storage.set("tokens_for_sale", tokens_for_sale)
        self.storage.set("start_time", start_time)
        self.storage.set("end_time", end_time)
        self.storage.set("start_price", start_price)
        self.storage.set("end_price", end_price)
        self.storage.set("min_funding_goal", min_funding_goal)

        self.storage.set("total_reserve_committed", U128(0))
        self.storage.set("state", AuctionState.ACTIVE)
        self.storage.set("clearing_price", U128(0))
        self.storage.set("finalized", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "tokens_for_sale": tokens_for_sale,
            "start_time": start_time,
            "end_time": end_time,
            "start_price": start_price,
            "end_price": end_price,
        })

    @external
    def fund_auction_tokens(self, admin: Address):
        """Transfer the launch tokens to be sold into the contract. (Admin only)

        Args:
            admin: Admin address.
        """
        self._require_initialized()
        admin.require_auth()
        self._require_admin(admin)

        tokens_for_sale = self.storage.get("tokens_for_sale")
        launch_token = self.storage.get("launch_token")

        self.env.invoke_contract(
            launch_token,
            "transfer",
            [admin, self.env.current_contract_address(), tokens_for_sale]
        )

        self.storage.set("tokens_funded", True)
        self.env.emit_event("auction_funded", {"amount": tokens_for_sale})

    @external
    def place_bid(self, caller: Address, reserve_amount: U128, max_price: U128) -> U128:
        """Place a bid in the auction committing reserve tokens.

        Args:
            caller: Bidder address.
            reserve_amount: Amount of reserve tokens to commit.
            max_price: Maximum token price the bidder accepts (scaled by 1,000,000).
        """
        self._require_initialized()
        caller.require_auth()

        if not self.storage.get("tokens_funded", False):
            raise ContractError.INVALID_STATE

        # Verify auction state is active and times match
        state = self.storage.get("state")
        if state != AuctionState.ACTIVE:
            raise ContractError.AUCTION_NOT_ACTIVE

        now = self.env.ledger().timestamp()
        start_time = self.storage.get("start_time")
        end_time = self.storage.get("end_time")

        if now < start_time:
            raise ContractError.AUCTION_NOT_ACTIVE
        if now >= end_time:
            raise ContractError.AUCTION_ALREADY_ENDED

        if reserve_amount == 0:
            raise ContractError.INVALID_BID_AMOUNT

        # Calculate current decaying price
        current_price = self._calculate_current_price(now)
        if current_price > max_price:
            raise ContractError.PRICE_TOO_HIGH

        # Calculate remaining capacity before reaching early success clearing price
        # early success occurs when TotalReserve / current_price >= tokens_for_sale
        # i.e., TotalReserve >= tokens_for_sale * current_price / 1,000,000
        tokens_for_sale = self.storage.get("tokens_for_sale")
        max_reserve_capacity = (tokens_for_sale * current_price) / U128(1000000)
        
        total_committed = self.storage.get("total_reserve_committed")
        if total_committed >= max_reserve_capacity:
            # Already reached cap, finalize auction
            self._finalize_auction_success(current_price)
            raise ContractError.AUCTION_ALREADY_ENDED

        # Cap the bid to remaining capacity if it exceeds it
        allowed_reserve = reserve_amount
        remaining_capacity = max_reserve_capacity - total_committed
        
        reached_cap = False
        if reserve_amount >= remaining_capacity:
            allowed_reserve = remaining_capacity
            reached_cap = True

        # Transfer reserve tokens
        reserve_token = self.storage.get("reserve_token")
        self.env.invoke_contract(
            reserve_token,
            "transfer",
            [caller, self.env.current_contract_address(), allowed_reserve]
        )

        # Update bidder record
        previous_bid = self.storage.get(("bid", caller), U128(0))
        self.storage.set(("bid", caller), previous_bid + allowed_reserve)

        new_total_committed = total_committed + allowed_reserve
        self.storage.set("total_reserve_committed", new_total_committed)

        self.env.emit_event("bid_placed", {
            "bidder": caller,
            "amount": allowed_reserve,
            "total_committed": new_total_committed,
        })

        if reached_cap:
            self._finalize_auction_success(current_price)

        return allowed_reserve

    @external
    def finalize_auction(self) -> U32:
        """Trigger auction finalization after end_time. Determine final state and clearing price."""
        self._require_initialized()

        state = self.storage.get("state")
        if state != AuctionState.ACTIVE:
            return state

        now = self.env.ledger().timestamp()
        end_time = self.storage.get("end_time")
        if now < end_time:
            raise ContractError.INVALID_STATE

        total_committed = self.storage.get("total_reserve_committed")
        min_funding_goal = self.storage.get("min_funding_goal")

        if total_committed < min_funding_goal:
            # Failed to reach minimum funding goal
            self.storage.set("state", AuctionState.FAILED)
            self.storage.set("finalized", True)
            self.env.emit_event("auction_failed", {
                "total_committed": total_committed,
                "goal": min_funding_goal,
            })
            return AuctionState.FAILED
        else:
            # Successful auction, clearing price is the end_price
            end_price = self.storage.get("end_price")
            self._finalize_auction_success(end_price)
            return AuctionState.SUCCESSFUL

    @external
    def claim(self, caller: Address) -> Map:
        """Claim launch tokens and excess reserve refund (if successful) or full refund (if failed).

        Args:
            caller: Bidder address.
        """
        self._require_initialized()
        caller.require_auth()

        if not self.storage.get("finalized", False):
            # Finalize auction if possible
            self.finalize_auction()

        if self.storage.get(("claimed", caller), False):
            raise ContractError.ALREADY_CLAIMED

        bid_amount = self.storage.get(("bid", caller), U128(0))
        if bid_amount == 0:
            raise ContractError.NOTHING_TO_CLAIM

        self.storage.set(("claimed", caller), True)
        state = self.storage.get("state")
        
        res = Map()

        if state == AuctionState.SUCCESSFUL:
            clearing_price = self.storage.get("clearing_price")
            
            # tokens_bought = bid_amount * 1,000,000 / clearing_price
            tokens_bought = (bid_amount * U128(1000000)) / clearing_price
            
            # reserve_cost = tokens_bought * clearing_price / 1,000,000
            reserve_cost = (tokens_bought * clearing_price) / U128(1000000)
            
            # refund excess reserve due to rounding
            refund = bid_amount - reserve_cost

            # Transfer bought tokens
            launch_token = self.storage.get("launch_token")
            self.env.invoke_contract(
                launch_token,
                "transfer",
                [self.env.current_contract_address(), caller, tokens_bought]
            )

            # Transfer refund if any
            if refund > 0:
                reserve_token = self.storage.get("reserve_token")
                self.env.invoke_contract(
                    reserve_token,
                    "transfer",
                    [self.env.current_contract_address(), caller, refund]
                )

            res.set("tokens_claimed", tokens_bought)
            res.set("refund", refund)

            self.env.emit_event("claim_processed", {
                "user": caller,
                "tokens_bought": tokens_bought,
                "refund": refund,
            })

        elif state == AuctionState.FAILED:
            # Refund full bid amount
            reserve_token = self.storage.get("reserve_token")
            self.env.invoke_contract(
                reserve_token,
                "transfer",
                [self.env.current_contract_address(), caller, bid_amount]
            )

            res.set("tokens_claimed", U128(0))
            res.set("refund", bid_amount)

            self.env.emit_event("refund_processed", {
                "user": caller,
                "refund": bid_amount,
            })

        return res

    @external
    def withdraw_proceeds(self, admin: Address) -> Map:
        """Withdraw sale proceeds and unclaimed tokens. (Admin only)

        Args:
            admin: Admin address.
        """
        self._require_initialized()
        admin.require_auth()
        self._require_admin(admin)

        if not self.storage.get("finalized", False):
            self.finalize_auction()

        state = self.storage.get("state")
        if state != AuctionState.SUCCESSFUL:
            raise ContractError.INVALID_STATE

        if self.storage.get("proceeds_withdrawn", False):
            raise ContractError.ALREADY_CLAIMED

        self.storage.set("proceeds_withdrawn", True)

        # Calculate total proceeds collected from actual sold tokens
        # We can fetch the remaining reserve token balance in the contract
        # (excluding any user refunds waiting to be claimed)
        # But to be precise, the total proceeds is:
        # total_tokens_sold = total_reserve_committed * 1,000,000 / clearing_price
        # actual_proceeds = total_tokens_sold * clearing_price / 1,000,000
        total_committed = self.storage.get("total_reserve_committed")
        clearing_price = self.storage.get("clearing_price")
        
        total_tokens_sold = (total_committed * U128(1000000)) / clearing_price
        actual_proceeds = (total_tokens_sold * clearing_price) / U128(1000000)
        
        # Withdraw proceeds
        reserve_token = self.storage.get("reserve_token")
        self.env.invoke_contract(
            reserve_token,
            "transfer",
            [self.env.current_contract_address(), admin, actual_proceeds]
        )

        # Refund unsold launch tokens to admin
        tokens_for_sale = self.storage.get("tokens_for_sale")
        unsold_tokens = tokens_for_sale - total_tokens_sold
        if unsold_tokens > 0:
            launch_token = self.storage.get("launch_token")
            self.env.invoke_contract(
                launch_token,
                "transfer",
                [self.env.current_contract_address(), admin, unsold_tokens]
            )

        res = Map()
        res.set("proceeds", actual_proceeds)
        res.set("unsold_tokens", unsold_tokens)

        self.env.emit_event("proceeds_withdrawn", {
            "admin": admin,
            "proceeds": actual_proceeds,
            "unsold_tokens": unsold_tokens,
        })

        return res

    @view
    def get_current_price(self) -> U128:
        """Get the current price based on the decay schedule."""
        now = self.env.ledger().timestamp()
        return self._calculate_current_price(now)

    @view
    def get_bid(self, user: Address) -> U128:
        """Get the bid amount deposited by a user."""
        return self.storage.get(("bid", user), U128(0))

    @view
    def get_status(self) -> Map:
        """Retrieve auction status details."""
        res = Map()
        res.set("state", self.storage.get("state"))
        res.set("clearing_price", self.storage.get("clearing_price"))
        res.set("total_committed", self.storage.get("total_reserve_committed"))
        res.set("finalized", self.storage.get("finalized"))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _calculate_current_price(self, timestamp: U64) -> U128:
        start_time = self.storage.get("start_time")
        end_time = self.storage.get("end_time")
        start_price = self.storage.get("start_price")
        end_price = self.storage.get("end_price")

        if timestamp <= start_time:
            return start_price
        if timestamp >= end_time:
            return end_price

        # Linear decay: price = start - (start - end) * (time - start_time) / (end_time - start_time)
        elapsed = U128(timestamp - start_time)
        total_duration = U128(end_time - start_time)
        price_diff = start_price - end_price

        decay = (price_diff * elapsed) / total_duration
        return start_price - decay

    def _finalize_auction_success(self, price: U128):
        self.storage.set("state", AuctionState.SUCCESSFUL)
        self.storage.set("clearing_price", price)
        self.storage.set("finalized", True)
        self.env.emit_event("auction_successful", {"clearing_price": price})
