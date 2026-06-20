"""
Private Sealed-Bid Auction — Sealed bid commit-reveal, reserve thresholds, collateral locks, and tie-breaking.

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
    TRANSFER_FAILED = 4
    AUCTION_NOT_FOUND = 5
    COMMIT_PHASE_CLOSED = 6
    REVEAL_PHASE_CLOSED = 7
    REVEAL_PHASE_NOT_OPEN = 8
    NO_COMMITMENT_FOUND = 9
    INVALID_BID_REVEAL = 10
    ALREADY_REVEALED = 11
    BELOW_RESERVE_PRICE = 12
    AUCTION_ALREADY_COMPLETED = 13
    ALREADY_BID = 14
    INVALID_COLLATERAL = 15


@contract
class PrivateAuctionSystem:
    """Manages sealed-bid auctions using a secure commit-reveal pattern, collateral staking, and tie-breakers."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, bid_token: Address):
        """Initialize the Private Auction contract."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("bid_token", bid_token)
        self.storage.set("auction_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin, "bid_token": bid_token})

    # ------------------------------------------------------------------ #
    #  Seller Operations                                                  #
    # ------------------------------------------------------------------ #

    @external
    def create_auction(
        self,
        seller: Address,
        reserve_price: U128,
        collateral_requirement: U128,
        commit_duration: U64,
        reveal_duration: U64
    ) -> U64:
        """Create a new private auction with reserve thresholds and phase timing. Seller only."""
        self._require_initialized()
        seller.require_auth()

        if collateral_requirement == U128(0):
            raise ContractError.INVALID_COLLATERAL

        a_id = self.storage.get("auction_count") + U64(1)
        self.storage.set("auction_count", a_id)

        now = self.env.ledger().timestamp()

        null_addr = Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF")

        auction = {
            "id": a_id,
            "seller": seller,
            "reserve_price": reserve_price,
            "collateral_requirement": collateral_requirement,
            "commit_deadline": now + commit_duration,
            "reveal_deadline": now + commit_duration + reveal_duration,
            "highest_bid": U128(0),
            "highest_bidder": null_addr,
            "highest_bid_time": U64(0),
            "completed": False
        }

        self.storage.set(("auction", a_id), auction)

        self.env.emit_event("auction_created", {
            "id": a_id,
            "seller": seller,
            "reserve": reserve_price,
            "collateral": collateral_requirement
        })

        return a_id

    # ------------------------------------------------------------------ #
    #  Bidder Operations                                                  #
    # ------------------------------------------------------------------ #

    @external
    def commit_bid(self, bidder: Address, auction_id: U64, commitment: Bytes) -> Bool:
        """Submit a sealed bid commitment, locking the required collateral in the process."""
        self._require_initialized()
        bidder.require_auth()

        a = self.storage.get(("auction", auction_id), None)
        if a is None:
            raise ContractError.AUCTION_NOT_FOUND

        now = self.env.ledger().timestamp()
        if now > a["commit_deadline"]:
            raise ContractError.COMMIT_PHASE_CLOSED

        # Check if already bid
        if self.storage.get(("bid_commitment", auction_id, bidder), None) is not None:
            raise ContractError.ALREADY_BID

        # Lock collateral (to prevent bidding without backing)
        collateral = a["collateral_requirement"]
        bid_token = self.storage.get("bid_token")
        contract_addr = self.env.current_contract_address()

        success = self.env.invoke_contract(bid_token, "transfer", [bidder, contract_addr, collateral])
        if not success:
            raise ContractError.TRANSFER_FAILED

        self.storage.set(("bid_commitment", auction_id, bidder), commitment)
        self.storage.set(("bidder_collateral", auction_id, bidder), collateral)
        self.storage.set(("bidder_commit_time", auction_id, bidder), now)

        self.env.emit_event("bid_committed", {
            "auction_id": auction_id,
            "bidder": bidder,
            "commitment": commitment
        })

        return True

    @external
    def reveal_bid(
        self,
        bidder: Address,
        auction_id: U64,
        bid_amount: U128,
        salt: Bytes
    ) -> Bool:
        """Reveal bid parameters. Updates the highest bid and checks reserve/tie-breaking conditions."""
        self._require_initialized()
        bidder.require_auth()

        a = self.storage.get(("auction", auction_id), None)
        if a is None:
            raise ContractError.AUCTION_NOT_FOUND

        now = self.env.ledger().timestamp()
        if now < a["commit_deadline"]:
            raise ContractError.REVEAL_PHASE_NOT_OPEN
        if now > a["reveal_deadline"]:
            raise ContractError.REVEAL_PHASE_CLOSED

        commitment = self.storage.get(("bid_commitment", auction_id, bidder), None)
        if commitment is None:
            raise ContractError.NO_COMMITMENT_FOUND

        # Cryptographic verification: hash(bidder + bid_amount + salt) == commitment
        expected_hash = self.env.crypto().keccak256(bidder, bid_amount, salt)
        if expected_hash != commitment:
            raise ContractError.INVALID_BID_REVEAL

        # Ensure they had enough collateral to back their bid
        collateral = self.storage.get(("bidder_collateral", auction_id, bidder), U128(0))
        if collateral < bid_amount:
            # Bidder cannot bid higher than locked collateral
            raise ContractError.BELOW_RESERVE_PRICE

        # Save bid reveal state to prevent double reveal
        self.storage.set(("bid_commitment", auction_id, bidder), None)

        commit_time = self.storage.get(("bidder_commit_time", auction_id, bidder), U64(0))

        # Check reserve price
        if bid_amount < a["reserve_price"]:
            # Refund collateral immediately if below reserve
            self._refund_bidder(auction_id, bidder, collateral)
            raise ContractError.BELOW_RESERVE_PRICE

        # Check bid ranking
        highest_bid = a["highest_bid"]
        is_higher = bid_amount > highest_bid
        is_tie_breaker = False

        if bid_amount == highest_bid:
            # Tie breaker: earlier commitment timestamp wins
            if commit_time < a["highest_bid_time"]:
                is_tie_breaker = True

        if is_higher or is_tie_breaker:
            # Refund previous highest bidder
            prev_highest = a["highest_bidder"]
            null_addr = Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF")
            if prev_highest != null_addr:
                prev_collateral = self.storage.get(("bidder_collateral", auction_id, prev_highest), U128(0))
                self._refund_bidder(auction_id, prev_highest, prev_collateral)

            # Record new leader
            a["highest_bid"] = bid_amount
            a["highest_bidder"] = bidder
            a["highest_bid_time"] = commit_time
            self.storage.set(("auction", auction_id), a)
        else:
            # Refund current bidder since their bid didn't win
            self._refund_bidder(auction_id, bidder, collateral)

        self.env.emit_event("bid_revealed", {
            "auction_id": auction_id,
            "bidder": bidder,
            "bid_amount": bid_amount
        })

        return True

    @external
    def finalize_auction(self, actor: Address, auction_id: U64):
        """Conclude the auction. Sends payment to seller and refunds excess collateral to winner."""
        self._require_initialized()
        actor.require_auth()

        a = self.storage.get(("auction", auction_id), None)
        if a is None:
            raise ContractError.AUCTION_NOT_FOUND

        now = self.env.ledger().timestamp()
        if now < a["reveal_deadline"]:
            raise ContractError.REVEAL_PHASE_CLOSED

        if a["completed"]:
            raise ContractError.AUCTION_ALREADY_COMPLETED

        winner = a["highest_bidder"]
        seller = a["seller"]
        winning_bid = a["highest_bid"]

        null_addr = Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF")

        if winner != null_addr:
            # Winner collateral allocation
            total_collateral = self.storage.get(("bidder_collateral", auction_id, winner), U128(0))
            excess = total_collateral - winning_bid

            bid_token = self.storage.get("bid_token")
            contract_addr = self.env.current_contract_address()

            # Pay seller
            success1 = self.env.invoke_contract(bid_token, "transfer", [contract_addr, seller, winning_bid])
            if not success1:
                raise ContractError.TRANSFER_FAILED

            # Refund winner excess collateral
            if excess > U128(0):
                success2 = self.env.invoke_contract(bid_token, "transfer", [contract_addr, winner, excess])
                if not success2:
                    raise ContractError.TRANSFER_FAILED

        a["completed"] = True
        self.storage.set(("auction", auction_id), a)

        self.env.emit_event("auction_finalized", {
            "auction_id": auction_id,
            "winner": winner,
            "winning_bid": winning_bid
        })

    # ------------------------------------------------------------------ #
    #  View Operations                                                    #
    # ------------------------------------------------------------------ #

    @view
    def get_auction_details(self, auction_id: U64) -> Map:
        """Get the current state and metrics of an auction."""
        self._require_initialized()
        a = self.storage.get(("auction", auction_id), None)
        if a is None:
            raise ContractError.AUCTION_NOT_FOUND
        
        res = Map()
        res.set(Symbol("seller"), a["seller"])
        res.set(Symbol("reserve_price"), a["reserve_price"])
        res.set(Symbol("commit_deadline"), a["commit_deadline"])
        res.set(Symbol("reveal_deadline"), a["reveal_deadline"])
        res.set(Symbol("completed"), a["completed"])

        # Protect bid privacy until completed
        if a["completed"] or self.env.ledger().timestamp() > a["reveal_deadline"]:
            res.set(Symbol("highest_bid"), a["highest_bid"])
            res.set(Symbol("highest_bidder"), a["highest_bidder"])
        else:
            res.set(Symbol("highest_bid"), U128(0))
            res.set(Symbol("highest_bidder"), Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                   #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        caller.require_auth()
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _refund_bidder(self, auction_id: U64, bidder: Address, amount: U128):
        """Refund locked collateral to a bidder."""
        if amount == U128(0):
            return
        
        bid_token = self.storage.get("bid_token")
        contract_addr = self.env.current_contract_address()
        success = self.env.invoke_contract(bid_token, "transfer", [contract_addr, bidder, amount])
        if not success:
            raise ContractError.TRANSFER_FAILED

        self.storage.set(("bidder_collateral", auction_id, bidder), U128(0))
