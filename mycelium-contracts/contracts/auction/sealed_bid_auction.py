"""
Sealed Bid Auction — Two-phase commit-reveal, bid hashes registry, reveal period, refund of unrevealed.

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
    INVALID_PHASE = 4
    INVALID_HASH = 5
    INSUFFICIENT_PAYMENT = 6
    AUCTION_ACTIVE = 7
    INSUFFICIENT_BALANCE = 8
    ZERO_AMOUNT = 9
    NO_BIDS_REVEALED = 10


class AuctionPhase:
    COMMIT = 0
    REVEAL = 1
    FINISHED = 2


@contract
class SealedBidAuction:
    """A commit-reveal sealed-bid auction contract for tokenized assets."""

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
        commit_duration: U64,
        reveal_duration: U64,
        min_bid: U128,
        reveal_deposit: U128, # Deposit required during commit phase
    ):
        """Initialize the sealed bid auction.

        Args:
            admin: Admin address.
            seller: Seller.
            asset_token: Asset token.
            asset_amount: Asset quantity.
            collateral_token: Bid payment token.
            commit_duration: Time in seconds for commit phase.
            reveal_duration: Time in seconds for reveal phase.
            min_bid: Minimum bid size.
            reveal_deposit: Collateral deposit required to lock a commit hash.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        now = self.env.ledger().timestamp()

        self.storage.set("admin", admin)
        self.storage.set("seller", seller)
        self.storage.set("asset_token", asset_token)
        self.storage.set("asset_amount", asset_amount)
        self.storage.set("collateral_token", collateral_token)
        self.storage.set("min_bid", min_bid)
        self.storage.set("reveal_deposit", reveal_deposit)

        self.storage.set("commit_end", now + commit_duration)
        self.storage.set("reveal_end", now + commit_duration + reveal_duration)

        self.storage.set("highest_revealed_bid", U128(0))
        self.storage.set("winner", Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"))
        self.storage.set("finalized", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "seller": seller,
            "commit_end": now + commit_duration,
            "reveal_end": now + commit_duration + reveal_duration,
        })

    @external
    def commit_bid(self, bidder: Address, bid_hash: Bytes) -> U128:
        """Commit a sealed bid hash. Requires posting the reveal deposit.

        Args:
            bidder: Bidder address.
            bid_hash: Keccak/SHA256 hash of (bid_amount, nonce).
        """
        self._require_initialized()
        bidder.require_auth()

        phase = self._current_phase()
        if phase != AuctionPhase.COMMIT:
            raise ContractError.INVALID_PHASE

        # Check if already committed
        if self.storage.get(("commit", bidder)) is not None:
            raise ContractError.INVALID_PHASE

        deposit = self.storage.get("reveal_deposit")
        token = self.storage.get("collateral_token")

        # Escrow commitment deposit
        success = self.env.invoke_contract(
            token,
            "transfer",
            [bidder, self.env.current_contract_address(), deposit]
        )
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        self.storage.set(("commit", bidder), bid_hash)
        self.storage.set(("deposit", bidder), deposit)
        self.storage.set(("revealed", bidder), False)

        self.env.emit_event("bid_committed", {
            "bidder": bidder,
            "deposit": deposit,
        })

        return deposit

    @external
    def reveal_bid(self, bidder: Address, bid_amount: U128, nonce: Bytes) -> U128:
        """Reveal the committed bid by showing the amount and nonce.

        Args:
            bidder: Bidder address.
            bid_amount: Revealed bid amount.
            nonce: Nonce used for commit hash creation.
        """
        self._require_initialized()
        bidder.require_auth()

        phase = self._current_phase()
        if phase != AuctionPhase.REVEAL:
            raise ContractError.INVALID_PHASE

        if self.storage.get(("revealed", bidder), False):
            raise ContractError.INVALID_PHASE

        # Verify hash match
        committed_hash = self.storage.get(("commit", bidder), None)
        if committed_hash is None:
            raise ContractError.INVALID_HASH

        # Compute hash: SHA256 of (bid_amount, nonce)
        # Format: we concatenate bid_amount bytes and nonce bytes
        # In python smart contracts, we can serialize or simple concatenation
        amount_bytes = self._u128_to_bytes(bid_amount)
        # Concatenate bytes:
        data = Bytes()
        data.append(amount_bytes)
        data.append(nonce)

        calculated_hash = self.env.crypto().sha256(data)
        if calculated_hash != committed_hash:
            raise ContractError.INVALID_HASH

        min_bid = self.storage.get("min_bid")
        if bid_amount < min_bid:
            raise ContractError.INSUFFICIENT_PAYMENT

        # Lock the rest of the bid amount
        deposit = self.storage.get(("deposit", bidder))
        token = self.storage.get("collateral_token")

        if bid_amount > deposit:
            extra_needed = bid_amount - deposit
            success = self.env.invoke_contract(
                token,
                "transfer",
                [bidder, self.env.current_contract_address(), extra_needed]
            )
            if not success:
                raise ContractError.INSUFFICIENT_BALANCE
            self.storage.set(("deposit", bidder), bid_amount)
        else:
            # Bid amount <= deposit, refund excess
            excess = deposit - bid_amount
            if excess > U128(0):
                self.env.invoke_contract(
                    token,
                    "transfer",
                    [self.env.current_contract_address(), bidder, excess]
                )
            self.storage.set(("deposit", bidder), bid_amount)

        self.storage.set(("revealed", bidder), True)
        self.storage.set(("revealed_amount", bidder), bid_amount)

        # Track highest bid
        highest = self.storage.get("highest_revealed_bid")
        if bid_amount > highest:
            self.storage.set("highest_revealed_bid", bid_amount)
            self.storage.set("winner", bidder)

        self.env.emit_event("bid_revealed", {
            "bidder": bidder,
            "amount": bid_amount,
        })

        return bid_amount

    @external
    def finalize(self, caller: Address):
        """Finalize the auction. Winner gets asset, seller gets winning bid.

        Args:
            caller: Trigger address.
        """
        self._require_initialized()
        caller.require_auth()

        phase = self._current_phase()
        if phase != AuctionPhase.FINISHED:
            raise ContractError.INVALID_PHASE

        if self.storage.get("finalized", False):
            raise ContractError.INVALID_PHASE

        self.storage.set("finalized", True)

        winner = self.storage.get("winner")
        seller = self.storage.get("seller")
        asset_token = self.storage.get("asset_token")
        asset_amount = self.storage.get("asset_amount")
        token = self.storage.get("collateral_token")

        winning_bid = self.storage.get("highest_revealed_bid")

        if winning_bid > U128(0):
            # Send asset to winner
            self.env.invoke_contract(
                asset_token,
                "transfer",
                [self.env.current_contract_address(), winner, asset_amount]
            )
            # Send bid funds to seller
            self.env.invoke_contract(
                token,
                "transfer",
                [self.env.current_contract_address(), seller, winning_bid]
            )
            # Winner's deposit balance tracks that they spent their bid
            self.storage.set(("deposit", winner), U128(0))

            self.env.emit_event("auction_finalized", {
                "winner": winner,
                "payout": winning_bid,
                "success": True,
            })
        else:
            # No bids revealed, return asset to seller
            self.env.invoke_contract(
                asset_token,
                "transfer",
                [self.env.current_contract_address(), seller, asset_amount]
            )
            self.env.emit_event("auction_finalized", {
                "winner": Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"),
                "payout": U128(0),
                "success": False,
            })

    @external
    def reclaim_funds(self, claimant: Address) -> U128:
        """Reclaim locked deposits. Can be called by losers or unrevealed bid committers.

        Args:
            claimant: Bidder.
        """
        self._require_initialized()
        claimant.require_auth()

        phase = self._current_phase()
        # Only allow reclaims once reveal phase is over
        if phase != AuctionPhase.FINISHED:
            raise ContractError.INVALID_PHASE

        winner = self.storage.get("winner")
        if claimant == winner and self.storage.get("highest_revealed_bid") > U128(0):
            # Winner cannot reclaim their bid
            raise ContractError.UNAUTHORIZED

        deposit = self.storage.get(("deposit", claimant), U128(0))
        if deposit == U128(0):
            raise ContractError.ZERO_AMOUNT

        self.storage.set(("deposit", claimant), U128(0))
        token = self.storage.get("collateral_token")

        self.env.invoke_contract(
            token,
            "transfer",
            [self.env.current_contract_address(), claimant, deposit]
        )

        self.env.emit_event("funds_reclaimed", {
            "bidder": claimant,
            "amount": deposit,
        })

        return deposit

    @view
    def get_phase(self) -> U64:
        """Get current phase index."""
        return U64(self._current_phase())

    @view
    def get_auction_status(self) -> Map:
        """Get current status details."""
        res = Map()
        res.set("winner", self.storage.get("winner"))
        res.set("highest_revealed_bid", self.storage.get("highest_revealed_bid"))
        res.set("finalized", self.storage.get("finalized"))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _current_phase(self) -> U64:
        now = self.env.ledger().timestamp()
        commit_end = self.storage.get("commit_end")
        reveal_end = self.storage.get("reveal_end")

        if now < commit_end:
            return AuctionPhase.COMMIT
        elif now < reveal_end:
            return AuctionPhase.REVEAL
        else:
            return AuctionPhase.FINISHED

    def _u128_to_bytes(self, val: U128) -> Bytes:
        # Simple serialization helper: converts U128 to a 16-byte representation
        res = Bytes()
        temp = val
        for _ in range(16):
            res.append(Bytes.from_slice([int(temp & U128(0xFF))]))
            temp = temp >> 8
        return res
