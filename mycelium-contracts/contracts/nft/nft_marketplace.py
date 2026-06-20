"""
NFT Marketplace — Core marketplace functionality.

Mycelium Smart Contract for Stellar. Allows users to list NFTs, place offers,
and run English auctions with anti-sniping bid extensions, platform fees,
and royalty splits.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)

class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    PAUSED = 4
    INVALID_PRICE = 5
    LISTING_NOT_FOUND = 6
    OFFER_NOT_FOUND = 7
    OFFER_EXPIRED = 8
    AUCTION_NOT_FOUND = 9
    AUCTION_NOT_STARTED = 10
    AUCTION_ENDED = 11
    BID_TOO_LOW = 12
    AUCTION_ACTIVE = 13
    ROYALTY_TOO_HIGH = 14
    INVALID_DURATION = 15
    INSUFFICIENT_FUNDS = 16

@contract
class NFTMarketplace:
    """
    Stellar Mycelium contract for peer-to-peer NFT trading.
    Includes listings, offers, auctions with anti-sniping, and royalty splits.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, platform_fee_bps: U64, fee_recipient: Address, payment_token: Address):
        """
        Initialize the marketplace contract parameters.
        
        Args:
            admin: Marketplace owner/admin address.
            platform_fee_bps: Fee in basis points (100 = 1%).
            fee_recipient: Address receiving platform fees.
            payment_token: Address of Stellar asset contract used for payments.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("platform_fee_bps", platform_fee_bps)
        self.storage.set("fee_recipient", fee_recipient)
        self.storage.set("payment_token", payment_token)
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "platform_fee_bps": platform_fee_bps,
            "fee_recipient": fee_recipient
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause listing and auction creation."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    @external
    def update_platform_fees(self, caller: Address, new_bps: U64, new_recipient: Address):
        """Update platform fee settings."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("platform_fee_bps", new_bps)
        self.storage.set("fee_recipient", new_recipient)
        self.env.emit_event("fees_updated", {"bps": new_bps, "recipient": new_recipient})

    @external
    def register_royalty(self, caller: Address, nft_contract: Address, recipient: Address, bps: U64):
        """Register royalty settings for an NFT contract (creator/royalty setup)."""
        caller.require_auth()
        self._require_initialized()
        if bps > U64(2500):  # Maximum 25% royalty
            raise ContractError.ROYALTY_TOO_HIGH

        self.storage.set(f"royalty_recipient_{nft_contract}", recipient)
        self.storage.set(f"royalty_bps_{nft_contract}", bps)
        self.env.emit_event("royalty_registered", {
            "nft_contract": nft_contract,
            "recipient": recipient,
            "bps": bps
        })

    # --- LISTING OPERATIONS ---

    @external
    def list_item(self, caller: Address, nft_contract: Address, token_id: U64, price: U128):
        """List an NFT for sale at a fixed price."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if price == U128(0):
            raise ContractError.INVALID_PRICE

        # Transfer NFT to marketplace for escrow
        self._escrow_nft(nft_contract, caller, self.env.current_contract_address(), token_id)

        self.storage.set(f"listing_seller_{nft_contract}_{token_id}", caller)
        self.storage.set(f"listing_price_{nft_contract}_{token_id}", price)

        self.env.emit_event("item_listed", {
            "nft_contract": nft_contract,
            "token_id": token_id,
            "seller": caller,
            "price": price
        })

    @external
    def cancel_listing(self, caller: Address, nft_contract: Address, token_id: U64):
        """Cancel a listing and retrieve the NFT."""
        caller.require_auth()
        self._require_initialized()

        seller = self.storage.get(f"listing_seller_{nft_contract}_{token_id}")
        if seller is None:
            raise ContractError.LISTING_NOT_FOUND
        if caller != seller:
            raise ContractError.UNAUTHORIZED

        self._cleanup_listing(nft_contract, token_id)

        # Transfer NFT back to seller
        self._release_nft(nft_contract, self.env.current_contract_address(), seller, token_id)

        self.env.emit_event("listing_cancelled", {
            "nft_contract": nft_contract,
            "token_id": token_id,
            "seller": seller
        })

    @external
    def buy_item(self, caller: Address, nft_contract: Address, token_id: U64):
        """Purchase listed NFT at the listing price."""
        caller.require_auth()
        self._require_initialized()

        seller = self.storage.get(f"listing_seller_{nft_contract}_{token_id}")
        price = self.storage.get(f"listing_price_{nft_contract}_{token_id}")

        if seller is None or price is None:
            raise ContractError.LISTING_NOT_FOUND

        self._cleanup_listing(nft_contract, token_id)

        # Distribute payments
        self._distribute_payment(caller, seller, price, nft_contract)

        # Transfer NFT to buyer
        self._release_nft(nft_contract, self.env.current_contract_address(), caller, token_id)

        self.env.emit_event("item_sold", {
            "nft_contract": nft_contract,
            "token_id": token_id,
            "seller": seller,
            "buyer": caller,
            "price": price
        })

    # --- OFFER OPERATIONS ---

    @external
    def make_offer(self, caller: Address, nft_contract: Address, token_id: U64, price: U128, duration_sec: U64):
        """Place a binding offer on an NFT."""
        caller.require_auth()
        self._require_initialized()
        if price == U128(0):
            raise ContractError.INVALID_PRICE
        if duration_sec == U64(0):
            raise ContractError.INVALID_DURATION

        expiry = self._get_now() + duration_sec
        self.storage.set(f"offer_price_{nft_contract}_{token_id}_{caller}", price)
        self.storage.set(f"offer_expiry_{nft_contract}_{token_id}_{caller}", expiry)

        self.env.emit_event("offer_made", {
            "nft_contract": nft_contract,
            "token_id": token_id,
            "offerer": caller,
            "price": price,
            "expiry": expiry
        })

    @external
    def accept_offer(self, caller: Address, nft_contract: Address, token_id: U64, offerer: Address):
        """Accept an outstanding offer. Caller must be NFT owner or listing seller."""
        caller.require_auth()
        self._require_initialized()

        price = self.storage.get(f"offer_price_{nft_contract}_{token_id}_{offerer}")
        expiry = self.storage.get(f"offer_expiry_{nft_contract}_{token_id}_{offerer}")

        if price is None or expiry is None:
            raise ContractError.OFFER_NOT_FOUND
        if self._get_now() > expiry:
            raise ContractError.OFFER_EXPIRED

        # Verify caller owns NFT or it is currently listed by caller
        is_listed = self.storage.get(f"listing_seller_{nft_contract}_{token_id}") == caller

        self.storage.remove(f"offer_price_{nft_contract}_{token_id}_{offerer}")
        self.storage.remove(f"offer_expiry_{nft_contract}_{token_id}_{offerer}")

        if is_listed:
            self._cleanup_listing(nft_contract, token_id)
            # Escrow is already holding the NFT, transfer payment
            self._distribute_payment(offerer, caller, price, nft_contract)
            self._release_nft(nft_contract, self.env.current_contract_address(), offerer, token_id)
        else:
            # P2P instant transfer from caller to offerer
            self._distribute_payment(offerer, caller, price, nft_contract)
            self._escrow_nft(nft_contract, caller, offerer, token_id)

        self.env.emit_event("offer_accepted", {
            "nft_contract": nft_contract,
            "token_id": token_id,
            "seller": caller,
            "buyer": offerer,
            "price": price
        })

    # --- ENGLISH AUCTION OPERATIONS ---

    @external
    def create_auction(self, caller: Address, nft_contract: Address, token_id: U64, reserve_price: U128, start_time: U64, end_time: U64):
        """Create an English auction for an NFT."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if end_time <= start_time or start_time < self._get_now():
            raise ContractError.INVALID_DURATION

        self._escrow_nft(nft_contract, caller, self.env.current_contract_address(), token_id)

        self.storage.set(f"auction_seller_{nft_contract}_{token_id}", caller)
        self.storage.set(f"auction_reserve_{nft_contract}_{token_id}", reserve_price)
        self.storage.set(f"auction_start_{nft_contract}_{token_id}", start_time)
        self.storage.set(f"auction_end_{nft_contract}_{token_id}", end_time)
        self.storage.set(f"auction_highest_bid_{nft_contract}_{token_id}", U128(0))
        self.storage.set(f"auction_highest_bidder_{nft_contract}_{token_id}", caller) # Placeholder

        self.env.emit_event("auction_created", {
            "nft_contract": nft_contract,
            "token_id": token_id,
            "seller": caller,
            "reserve_price": reserve_price,
            "start_time": start_time,
            "end_time": end_time
        })

    @external
    def bid(self, caller: Address, nft_contract: Address, token_id: U64, amount: U128):
        """Place a bid on an active auction. Implements anti-sniping extension."""
        caller.require_auth()
        self._require_initialized()

        seller = self.storage.get(f"auction_seller_{nft_contract}_{token_id}")
        if seller is None:
            raise ContractError.AUCTION_NOT_FOUND

        start = self.storage.get(f"auction_start_{nft_contract}_{token_id}")
        end = self.storage.get(f"auction_end_{nft_contract}_{token_id}")
        now = self._get_now()

        if now < start:
            raise ContractError.AUCTION_NOT_STARTED
        if now > end:
            raise ContractError.AUCTION_ENDED

        highest_bid = self.storage.get(f"auction_highest_bid_{nft_contract}_{token_id}")
        reserve = self.storage.get(f"auction_reserve_{nft_contract}_{token_id}")

        min_bid = reserve if highest_bid == U128(0) else highest_bid + (highest_bid / U128(20)) # 5% minimum increment
        if amount < min_bid:
            raise ContractError.BID_TOO_LOW

        # Return previous highest bid if it existed
        previous_bidder = self.storage.get(f"auction_highest_bidder_{nft_contract}_{token_id}")
        if highest_bid > U128(0) and previous_bidder != seller:
            self._pay(previous_bidder, highest_bid)

        # Lock bid funds by transferring to escrow/marketplace
        self._collect_payment(caller, self.env.current_contract_address(), amount)

        # Save new bid details
        self.storage.set(f"auction_highest_bid_{nft_contract}_{token_id}", amount)
        self.storage.set(f"auction_highest_bidder_{nft_contract}_{token_id}", caller)

        # Anti-sniping bid extension: if bid placed in last 5 minutes (300 secs), extend end time by 5 minutes
        time_left = end - now
        if time_left < U64(300):
            new_end = now + U64(300)
            self.storage.set(f"auction_end_{nft_contract}_{token_id}", new_end)
            self.env.emit_event("auction_extended", {
                "nft_contract": nft_contract,
                "token_id": token_id,
                "new_end_time": new_end
            })

        self.env.emit_event("bid_placed", {
            "nft_contract": nft_contract,
            "token_id": token_id,
            "bidder": caller,
            "amount": amount
        })

    @external
    def end_auction(self, caller: Address, nft_contract: Address, token_id: U64):
        """End the auction. Transfer NFT to highest bidder and distribute funds."""
        self._require_initialized()

        seller = self.storage.get(f"auction_seller_{nft_contract}_{token_id}")
        if seller is None:
            raise ContractError.AUCTION_NOT_FOUND

        end = self.storage.get(f"auction_end_{nft_contract}_{token_id}")
        if self._get_now() <= end:
            raise ContractError.AUCTION_ACTIVE

        highest_bid = self.storage.get(f"auction_highest_bid_{nft_contract}_{token_id}")
        highest_bidder = self.storage.get(f"auction_highest_bidder_{nft_contract}_{token_id}")

        self._cleanup_auction(nft_contract, token_id)

        if highest_bid == U128(0) or highest_bidder == seller:
            # No bids or reserve not met, return NFT to seller
            self._release_nft(nft_contract, self.env.current_contract_address(), seller, token_id)
            self.env.emit_event("auction_settled_no_winner", {
                "nft_contract": nft_contract,
                "token_id": token_id,
                "seller": seller
            })
        else:
            # Pay seller, fees, and royalties
            self._distribute_payment_from_escrow(seller, highest_bid, nft_contract)
            # Transfer NFT to winning bidder
            self._release_nft(nft_contract, self.env.current_contract_address(), highest_bidder, token_id)

            self.env.emit_event("auction_settled", {
                "nft_contract": nft_contract,
                "token_id": token_id,
                "winner": highest_bidder,
                "amount": highest_bid
            })

    # --- VIEWS ---

    @view
    def get_listing(self, nft_contract: Address, token_id: U64) -> Map:
        """Get listing seller and price."""
        res = Map(self.env)
        seller = self.storage.get(f"listing_seller_{nft_contract}_{token_id}")
        price = self.storage.get(f"listing_price_{nft_contract}_{token_id}")
        if seller is not None:
            res.set("seller", seller)
            res.set("price", price)
        return res

    @view
    def get_auction(self, nft_contract: Address, token_id: U64) -> Map:
        """Get auction details."""
        res = Map(self.env)
        seller = self.storage.get(f"auction_seller_{nft_contract}_{token_id}")
        if seller is not None:
            res.set("seller", seller)
            res.set("reserve_price", self.storage.get(f"auction_reserve_{nft_contract}_{token_id}"))
            res.set("start_time", self.storage.get(f"auction_start_{nft_contract}_{token_id}"))
            res.set("end_time", self.storage.get(f"auction_end_{nft_contract}_{token_id}"))
            res.set("highest_bid", self.storage.get(f"auction_highest_bid_{nft_contract}_{token_id}"))
            res.set("highest_bidder", self.storage.get(f"auction_highest_bidder_{nft_contract}_{token_id}"))
        return res

    # --- INTERNAL HELPERS ---

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_not_paused(self):
        if self.storage.get("paused", False):
            raise ContractError.PAUSED

    def _require_admin(self, caller: Address):
        if caller != self.storage.get("admin"):
            raise ContractError.UNAUTHORIZED

    def _get_now(self) -> U64:
        return self.env.ledger_timestamp()

    def _cleanup_listing(self, nft_contract: Address, token_id: U64):
        self.storage.remove(f"listing_seller_{nft_contract}_{token_id}")
        self.storage.remove(f"listing_price_{nft_contract}_{token_id}")

    def _cleanup_auction(self, nft_contract: Address, token_id: U64):
        self.storage.remove(f"auction_seller_{nft_contract}_{token_id}")
        self.storage.remove(f"auction_reserve_{nft_contract}_{token_id}")
        self.storage.remove(f"auction_start_{nft_contract}_{token_id}")
        self.storage.remove(f"auction_end_{nft_contract}_{token_id}")
        self.storage.remove(f"auction_highest_bid_{nft_contract}_{token_id}")
        self.storage.remove(f"auction_highest_bidder_{nft_contract}_{token_id}")

    def _escrow_nft(self, nft_contract: Address, from_addr: Address, to_addr: Address, token_id: U64):
        self.env.call(nft_contract, "transfer", from_addr, to_addr, token_id)

    def _release_nft(self, nft_contract: Address, from_addr: Address, to_addr: Address, token_id: U64):
        self.env.call(nft_contract, "transfer", from_addr, to_addr, token_id)

    def _collect_payment(self, from_addr: Address, to_addr: Address, amount: U128):
        token_address = self.storage.get("payment_token")
        self.env.call(token_address, "transfer", from_addr, to_addr, amount)

    def _pay(self, to_addr: Address, amount: U128):
        token_address = self.storage.get("payment_token")
        self.env.call(token_address, "transfer", self.env.current_contract_address(), to_addr, amount)

    def _distribute_payment(self, payer: Address, seller: Address, total_amount: U128, nft_contract: Address):
        """Distribute total sale amount: platform fee, royalty split, and seller balance."""
        platform_fee_bps = self.storage.get("platform_fee_bps", U64(0))
        fee_recipient = self.storage.get("fee_recipient")
        token_address = self.storage.get("payment_token")

        platform_fee = (total_amount * U128(platform_fee_bps)) / U128(10000)
        royalty_fee = U128(0)
        royalty_recipient = self.storage.get(f"royalty_recipient_{nft_contract}")

        if royalty_recipient is not None:
            royalty_bps = self.storage.get(f"royalty_bps_{nft_contract}", U64(0))
            royalty_fee = (total_amount * U128(royalty_bps)) / U128(10000)

        seller_share = total_amount - platform_fee - royalty_fee

        if platform_fee > U128(0):
            self.env.call(token_address, "transfer", payer, fee_recipient, platform_fee)
        if royalty_fee > U128(0):
            self.env.call(token_address, "transfer", payer, royalty_recipient, royalty_fee)
        self.env.call(token_address, "transfer", payer, seller, seller_share)

    def _distribute_payment_from_escrow(self, seller: Address, total_amount: U128, nft_contract: Address):
        """Settle payments using funds already held in the contract escrow."""
        platform_fee_bps = self.storage.get("platform_fee_bps", U64(0))
        fee_recipient = self.storage.get("fee_recipient")
        token_address = self.storage.get("payment_token")

        platform_fee = (total_amount * U128(platform_fee_bps)) / U128(10000)
        royalty_fee = U128(0)
        royalty_recipient = self.storage.get(f"royalty_recipient_{nft_contract}")

        if royalty_recipient is not None:
            royalty_bps = self.storage.get(f"royalty_bps_{nft_contract}", U64(0))
            royalty_fee = (total_amount * U128(royalty_bps)) / U128(10000)

        seller_share = total_amount - platform_fee - royalty_fee

        if platform_fee > U128(0):
            self.env.call(token_address, "transfer", self.env.current_contract_address(), fee_recipient, platform_fee)
        if royalty_fee > U128(0):
            self.env.call(token_address, "transfer", self.env.current_contract_address(), royalty_recipient, royalty_fee)
        self.env.call(token_address, "transfer", self.env.current_contract_address(), seller, seller_share)
