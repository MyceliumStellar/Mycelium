"""
Vickrey Auction — Sealed bid second-price payout, bid deposit escrow, validation, dispute rules.

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
    DISPUTE_FAILED = 10


class AuctionPhase:
    COMMIT = 0
    REVEAL = 1
    FINISHED = 2


@contract
class VickreyAuction:
    """A contract coordinating a second-price sealed-bid (Vickrey) auction with commit-reveal rules."""

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
        reveal_deposit: U128,
    ):
        """Initialize the Vickrey auction parameters.

        Args:
            admin: Admin address.
            seller: Seller address.
            asset_token: Token address of the auctioned asset.
            asset_amount: Amount of asset tokens.
            collateral_token: Bid payment token.
            commit_duration: Time in seconds for commit phase.
            reveal_duration: Time in seconds for reveal phase.
            min_bid: Floor bid.
            reveal_deposit: Escrow commitment deposit.
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

        # Vickrey stats
        self.storage.set("highest_bid", U128(0))
        self.storage.set("highest_bidder", Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"))
        self.storage.set("second_highest_bid", min_bid) # If only 1 bidder, pays min_bid

        self.storage.set("finalized", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "seller": seller,
            "commit_end": now + commit_duration,
            "reveal_end": now + commit_duration + reveal_duration,
        })

    @external
    def commit_bid(self, bidder: Address, bid_hash: Bytes) -> U128:
        """Commit a sealed bid hash. Escrows commitment deposit.

        Args:
            bidder: Bidder address.
            bid_hash: Hash.
        """
        self._require_initialized()
        bidder.require_auth()

        phase = self._current_phase()
        if phase != AuctionPhase.COMMIT:
            raise ContractError.INVALID_PHASE

        if self.storage.get(("commit", bidder)) is not None:
            raise ContractError.INVALID_PHASE

        deposit = self.storage.get("reveal_deposit")
        token = self.storage.get("collateral_token")

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
        """Reveal the Vickrey bid. Locks the entire bid amount in escrow.

        Args:
            bidder: Bidder address.
            bid_amount: True bid amount.
            nonce: Nonce used for hash generation.
        """
        self._require_initialized()
        bidder.require_auth()

        phase = self._current_phase()
        if phase != AuctionPhase.REVEAL:
            raise ContractError.INVALID_PHASE

        if self.storage.get(("revealed", bidder), False):
            raise ContractError.INVALID_PHASE

        committed_hash = self.storage.get(("commit", bidder), None)
        if committed_hash is None:
            raise ContractError.INVALID_HASH

        amount_bytes = self._u128_to_bytes(bid_amount)
        data = Bytes()
        data.append(amount_bytes)
        data.append(nonce)

        calculated_hash = self.env.crypto().sha256(data)
        if calculated_hash != committed_hash:
            raise ContractError.INVALID_HASH

        min_bid = self.storage.get("min_bid")
        if bid_amount < min_bid:
            raise ContractError.INSUFFICIENT_PAYMENT

        # Escrow remaining bid amount
        deposit = self.storage.get(("deposit", bidder))
        token = self.storage.get("collateral_token")

        if bid_amount > deposit:
            extra = bid_amount - deposit
            success = self.env.invoke_contract(
                token,
                "transfer",
                [bidder, self.env.current_contract_address(), extra]
            )
            if not success:
                raise ContractError.INSUFFICIENT_BALANCE
            self.storage.set(("deposit", bidder), bid_amount)
        else:
            excess = deposit - bid_amount
            if excess > U128(0):
                self.env.invoke_contract(
                    token,
                    "transfer",
                    [self.env.current_contract_address(), bidder, excess]
                )
            self.storage.set(("deposit", bidder), bid_amount)

        self.storage.set(("revealed", bidder), True)

        # Update Vickrey pricing
        highest = self.storage.get("highest_bid")
        highest_bidder = self.storage.get("highest_bidder")
        second_highest = self.storage.get("second_highest_bid")

        if bid_amount > highest:
            # Current highest becomes second highest
            if highest > min_bid:
                self.storage.set("second_highest_bid", highest)
            else:
                self.storage.set("second_highest_bid", min_bid)

            self.storage.set("highest_bid", bid_amount)
            self.storage.set("highest_bidder", bidder)
        elif bid_amount > second_highest:
            self.storage.set("second_highest_bid", bid_amount)

        self.env.emit_event("bid_revealed", {
            "bidder": bidder,
            "amount": bid_amount,
        })

        return bid_amount

    @external
    def finalize(self, caller: Address):
        """Finalize the Vickrey auction. Winner pays second highest bid. Excess is refunded.

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

        winner = self.storage.get("highest_bidder")
        seller = self.storage.get("seller")
        asset_token = self.storage.get("asset_token")
        asset_amount = self.storage.get("asset_amount")
        token = self.storage.get("collateral_token")

        highest_bid = self.storage.get("highest_bid")
        second_highest = self.storage.get("second_highest_bid")

        if highest_bid > U128(0):
            # Winner pays second_highest_bid
            payout = second_highest
            # Refund excess to winner: winner_deposit - second_highest
            winner_deposit = self.storage.get(("deposit", winner))
            excess = winner_deposit - second_highest
            
            # Send asset to winner
            self.env.invoke_contract(
                asset_token,
                "transfer",
                [self.env.current_contract_address(), winner, asset_amount]
            )
            # Send payout to seller
            self.env.invoke_contract(
                token,
                "transfer",
                [self.env.current_contract_address(), seller, payout]
            )
            # Refund excess to winner
            if excess > U128(0):
                self.env.invoke_contract(
                    token,
                    "transfer",
                    [self.env.current_contract_address(), winner, excess]
                )

            # Clear winner deposit track
            self.storage.set(("deposit", winner), U128(0))

            self.env.emit_event("auction_finalized", {
                "winner": winner,
                "price_paid": payout,
                "success": True,
            })
        else:
            # No bids, return asset to seller
            self.env.invoke_contract(
                asset_token,
                "transfer",
                [self.env.current_contract_address(), seller, asset_amount]
            )
            self.env.emit_event("auction_finalized", {
                "winner": Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"),
                "price_paid": U128(0),
                "success": False,
            })

    @external
    def dispute_unrevealed(self, disputer: Address, unrevealed_bidder: Address) -> U128:
        """Slash the deposit of a bidder who committed but failed to reveal.
        Split slashed deposit: 50% to seller, 50% to disputer.

        Args:
            disputer: Caller raising dispute.
            unrevealed_bidder: Target bidder who did not reveal.
        """
        self._require_initialized()
        disputer.require_auth()

        phase = self._current_phase()
        if phase != AuctionPhase.FINISHED:
            raise ContractError.INVALID_PHASE

        committed_hash = self.storage.get(("commit", unrevealed_bidder), None)
        if committed_hash is None:
            raise ContractError.DISPUTE_FAILED

        revealed = self.storage.get(("revealed", unrevealed_bidder), False)
        if revealed:
            raise ContractError.DISPUTE_FAILED

        deposit = self.storage.get(("deposit", unrevealed_bidder), U128(0))
        if deposit == U128(0):
            raise ContractError.DISPUTE_FAILED

        # Clear deposit immediately
        self.storage.set(("deposit", unrevealed_bidder), U128(0))

        half_deposit = deposit / U128(2)
        token = self.storage.get("collateral_token")
        seller = self.storage.get("seller")

        # Distribute slashed funds
        self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), seller, half_deposit])
        self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), disputer, half_deposit])

        self.env.emit_event("dispute_slashed", {
            "slashed_bidder": unrevealed_bidder,
            "disputer": disputer,
            "slashed_amount": deposit,
        })

        return deposit

    @external
    def reclaim_funds(self, claimant: Address) -> U128:
        """Reclaim funds for losing revealed bidders.

        Args:
            claimant: Bidder.
        """
        self._require_initialized()
        claimant.require_auth()

        phase = self._current_phase()
        if phase != AuctionPhase.FINISHED:
            raise ContractError.INVALID_PHASE

        winner = self.storage.get("highest_bidder")
        if claimant == winner and self.storage.get("highest_bid") > U128(0):
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
    def get_auction_status(self) -> Map:
        """Get status of the Vickrey auction."""
        res = Map()
        res.set("winner", self.storage.get("highest_bidder"))
        res.set("highest_bid", self.storage.get("highest_bid"))
        res.set("second_highest_bid", self.storage.get("second_highest_bid"))
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
        res = Bytes()
        temp = val
        for _ in range(16):
            res.append(Bytes.from_slice([int(temp & U128(0xFF))]))
            temp = temp >> 8
        return res
