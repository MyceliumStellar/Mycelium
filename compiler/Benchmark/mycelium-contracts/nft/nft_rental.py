"""
NFT Rental Contract — Secure rental with user role management.

Mycelium Smart Contract for Stellar. Allows NFT owners to list their assets
for rent, renters to rent them by paying a daily rate in escrow, and supports
early returns with partial refunds, revenue splits, and role expiry.
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
    NOT_LISTED = 5
    RENTAL_ACTIVE = 6
    RENTAL_NOT_ACTIVE = 7
    RENTAL_EXPIRED = 8
    INVALID_DURATION = 9
    INVALID_RATE = 10
    INSUFFICIENT_FUNDS = 11
    SUBRENTAL_BLOCKED = 12
    NOT_RENTER = 13
    NOT_OWNER = 14

@contract
class NFTRental:
    """
    Stellar Mycelium contract for NFT rentals.
    Handles secure escrow of NFTs, temporary user role assignment,
    prepaid rents with partial refund on early returns, and co-owner revenue splits.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, payment_token: Address, platform_fee_bps: U64, fee_recipient: Address):
        """Initialize the rental contract."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("payment_token", payment_token)
        self.storage.set("platform_fee_bps", platform_fee_bps)
        self.storage.set("fee_recipient", fee_recipient)
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "payment_token": payment_token,
            "platform_fee_bps": platform_fee_bps
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause contract operations."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    @external
    def list_for_rent(
        self,
        caller: Address,
        nft_contract: Address,
        token_id: U64,
        daily_rate: U128,
        max_duration_sec: U64,
        split_recipient: Address,
        split_bps: U64
    ):
        """
        List an NFT for rent. Transfers the NFT to the rental contract's escrow.
        
        Args:
            caller: Owner of the NFT.
            nft_contract: The contract address of the NFT.
            token_id: The ID of the token.
            daily_rate: Cost per 24 hours of rent.
            max_duration_sec: Maximum time the NFT can be rented in a single transaction.
            split_recipient: Optional co-owner or affiliate address to split rent revenue.
            split_bps: Percentage split for recipient (in basis points).
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if daily_rate == U128(0):
            raise ContractError.INVALID_RATE
        if max_duration_sec == U64(0):
            raise ContractError.INVALID_DURATION
        if split_bps > U64(10000):
            raise ContractError.INVALID_RATE

        # Escrow NFT in the rental contract
        self._escrow_nft(nft_contract, caller, self.env.current_contract_address(), token_id)

        # Record rental listing
        key_prefix = f"{nft_contract}_{token_id}"
        self.storage.set(f"rent_owner_{key_prefix}", caller)
        self.storage.set(f"rent_daily_rate_{key_prefix}", daily_rate)
        self.storage.set(f"rent_max_duration_{key_prefix}", max_duration_sec)
        self.storage.set(f"rent_split_recipient_{key_prefix}", split_recipient)
        self.storage.set(f"rent_split_bps_{key_prefix}", split_bps)
        self.storage.set(f"rent_status_{key_prefix}", U64(1))  # 1 = Listed, 2 = Rented

        self.env.emit_event("listed_for_rent", {
            "nft_contract": nft_contract,
            "token_id": token_id,
            "owner": caller,
            "daily_rate": daily_rate,
            "max_duration": max_duration_sec
        })

    @external
    def cancel_rent_listing(self, caller: Address, nft_contract: Address, token_id: U64):
        """Cancel a rent listing and withdraw the NFT. Can only be done if not currently rented."""
        caller.require_auth()
        self._require_initialized()

        key_prefix = f"{nft_contract}_{token_id}"
        owner = self.storage.get(f"rent_owner_{key_prefix}")
        if owner is None:
            raise ContractError.NOT_LISTED
        if caller != owner:
            raise ContractError.UNAUTHORIZED

        status = self.storage.get(f"rent_status_{key_prefix}", U64(0))
        if status == U64(2):
            raise ContractError.RENTAL_ACTIVE

        # Clean up storage
        self._cleanup_listing(key_prefix)

        # Release NFT
        self._release_nft(nft_contract, self.env.current_contract_address(), owner, token_id)

        self.env.emit_event("rent_listing_cancelled", {
            "nft_contract": nft_contract,
            "token_id": token_id,
            "owner": owner
        })

    @external
    def rent_nft(self, caller: Address, nft_contract: Address, token_id: U64, duration_sec: U64):
        """Rent the NFT for a specific duration, paying upfront rent into the contract escrow."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        key_prefix = f"{nft_contract}_{token_id}"
        owner = self.storage.get(f"rent_owner_{key_prefix}")
        if owner is None:
            raise ContractError.NOT_LISTED

        status = self.storage.get(f"rent_status_{key_prefix}", U64(0))
        if status == U64(2):
            raise ContractError.RENTAL_ACTIVE

        max_dur = self.storage.get(f"rent_max_duration_{key_prefix}", U64(0))
        if duration_sec > max_dur or duration_sec == U64(0):
            raise ContractError.INVALID_DURATION

        daily_rate = self.storage.get(f"rent_daily_rate_{key_prefix}", U128(0))
        # Total cost calculation: (daily_rate * duration_sec) / 86400
        total_rent = (daily_rate * U128(duration_sec)) / U128(86400)
        if total_rent == U128(0):
            total_rent = U128(1)  # Minimum 1 unit of token

        # Pull rent payment into contract escrow
        self._collect_payment(caller, self.env.current_contract_address(), total_rent)

        now = self._get_now()
        expiry = now + duration_sec

        # Update status
        self.storage.set(f"rent_status_{key_prefix}", U64(2))  # 2 = Rented
        self.storage.set(f"rent_renter_{key_prefix}", caller)
        self.storage.set(f"rent_start_time_{key_prefix}", now)
        self.storage.set(f"rent_expiry_{key_prefix}", expiry)
        self.storage.set(f"rent_paid_amount_{key_prefix}", total_rent)

        self.env.emit_event("nft_rented", {
            "nft_contract": nft_contract,
            "token_id": token_id,
            "renter": caller,
            "start_time": now,
            "expiry": expiry,
            "rent_paid": total_rent
        })

    @external
    def early_return(self, caller: Address, nft_contract: Address, token_id: U64):
        """Called by the renter. Ends the rental early, refunding unused rent and releasing the NFT."""
        caller.require_auth()
        self._require_initialized()

        key_prefix = f"{nft_contract}_{token_id}"
        renter = self.storage.get(f"rent_renter_{key_prefix}")
        if renter is None:
            raise ContractError.RENTAL_NOT_ACTIVE
        if caller != renter:
            raise ContractError.NOT_RENTER

        now = self._get_now()
        expiry = self.storage.get(f"rent_expiry_{key_prefix}", U64(0))
        if now >= expiry:
            raise ContractError.RENTAL_EXPIRED

        start = self.storage.get(f"rent_start_time_{key_prefix}", U64(0))
        total_paid = self.storage.get(f"rent_paid_amount_{key_prefix}", U128(0))
        owner = self.storage.get(f"rent_owner_{key_prefix}")

        # Calculations
        elapsed = now - start
        total_duration = expiry - start
        
        # Calculate used amount and refund amount
        used_amount = (total_paid * U128(elapsed)) / U128(total_duration)
        refund_amount = total_paid - used_amount

        # Distribute used amount to owner and co-owners, refund the rest to the renter
        self._distribute_rent_revenue(owner, used_amount, nft_contract, token_id)
        if refund_amount > U128(0):
            self._pay(renter, refund_amount)

        # Release NFT back to owner and clean up rental parameters (but keep listed status)
        self._reset_rental_state(key_prefix)
        self._release_nft(nft_contract, self.env.current_contract_address(), owner, token_id)

        self.env.emit_event("early_return", {
            "nft_contract": nft_contract,
            "token_id": token_id,
            "renter": renter,
            "refund": refund_amount,
            "owner_paid": used_amount
        })

    @external
    def claim_expired_rental(self, caller: Address, nft_contract: Address, token_id: U64):
        """Can be called by anyone once the rental expiry time has passed. Settle funds and returns NFT."""
        self._require_initialized()

        key_prefix = f"{nft_contract}_{token_id}"
        status = self.storage.get(f"rent_status_{key_prefix}", U64(0))
        if status != U64(2):
            raise ContractError.RENTAL_NOT_ACTIVE

        expiry = self.storage.get(f"rent_expiry_{key_prefix}", U64(0))
        if self._get_now() < expiry:
            raise ContractError.RENTAL_ACTIVE

        owner = self.storage.get(f"rent_owner_{key_prefix}")
        total_paid = self.storage.get(f"rent_paid_amount_{key_prefix}", U128(0))

        # Pay all remaining rent to the owner (no refunds)
        self._distribute_rent_revenue(owner, total_paid, nft_contract, token_id)

        # Release NFT back to owner
        self._reset_rental_state(key_prefix)
        self._release_nft(nft_contract, self.env.current_contract_address(), owner, token_id)

        self.env.emit_event("rental_expired_claimed", {
            "nft_contract": nft_contract,
            "token_id": token_id,
            "owner": owner,
            "total_rent_settled": total_paid
        })

    # --- VIEWS ---

    @view
    def get_rental_info(self, nft_contract: Address, token_id: U64) -> Map:
        """Returns details about the rental listing or active agreement."""
        res = Map(self.env)
        key_prefix = f"{nft_contract}_{token_id}"
        owner = self.storage.get(f"rent_owner_{key_prefix}")
        if owner is not None:
            res.set("owner", owner)
            res.set("daily_rate", self.storage.get(f"rent_daily_rate_{key_prefix}"))
            res.set("max_duration", self.storage.get(f"rent_max_duration_{key_prefix}"))
            res.set("status", self.storage.get(f"rent_status_{key_prefix}"))
            res.set("renter", self.storage.get(f"rent_renter_{key_prefix}", Address(self.env)))
            res.set("expiry", self.storage.get(f"rent_expiry_{key_prefix}", U64(0)))
        return res

    @view
    def get_active_user(self, nft_contract: Address, token_id: U64) -> Address:
        """Returns the active user of the NFT. If not rented, returns the owner."""
        key_prefix = f"{nft_contract}_{token_id}"
        status = self.storage.get(f"rent_status_{key_prefix}", U64(0))
        if status == U64(2):
            expiry = self.storage.get(f"rent_expiry_{key_prefix}", U64(0))
            if self._get_now() < expiry:
                return self.storage.get(f"rent_renter_{key_prefix}")
        
        # If not rented or expired, owner is the active user
        owner = self.storage.get(f"rent_owner_{key_prefix}")
        if owner is not None:
            return owner
        return Address(self.env) # Returns null address if not listed

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

    def _cleanup_listing(self, key_prefix: str):
        self.storage.remove(f"rent_owner_{key_prefix}")
        self.storage.remove(f"rent_daily_rate_{key_prefix}")
        self.storage.remove(f"rent_max_duration_{key_prefix}")
        self.storage.remove(f"rent_split_recipient_{key_prefix}")
        self.storage.remove(f"rent_split_bps_{key_prefix}")
        self.storage.remove(f"rent_status_{key_prefix}")
        self._reset_rental_state(key_prefix)

    def _reset_rental_state(self, key_prefix: str):
        self.storage.remove(f"rent_renter_{key_prefix}")
        self.storage.remove(f"rent_start_time_{key_prefix}")
        self.storage.remove(f"rent_expiry_{key_prefix}")
        self.storage.remove(f"rent_paid_amount_{key_prefix}")
        # Keep owner and listings, set status to 1 (Listed) or remove if listing is fully deleted
        if self.storage.get(f"rent_owner_{key_prefix}") is not None:
            self.storage.set(f"rent_status_{key_prefix}", U64(1))

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

    def _distribute_rent_revenue(self, owner: Address, amount: U128, nft_contract: Address, token_id: U64):
        """Distribute rent revenue: platform fee, split co-owner/affiliate, and owner share."""
        if amount == U128(0):
            return

        platform_fee_bps = self.storage.get("platform_fee_bps", U64(0))
        fee_recipient = self.storage.get("fee_recipient")
        token_address = self.storage.get("payment_token")

        platform_fee = (amount * U128(platform_fee_bps)) / U128(10000)

        key_prefix = f"{nft_contract}_{token_id}"
        split_recipient = self.storage.get(f"rent_split_recipient_{key_prefix}")
        split_bps = self.storage.get(f"rent_split_bps_{key_prefix}", U64(0))

        split_fee = U128(0)
        if split_recipient is not None and split_bps > U64(0):
            split_fee = (amount * U128(split_bps)) / U128(10000)

        owner_share = amount - platform_fee - split_fee

        if platform_fee > U128(0):
            self.env.call(token_address, "transfer", self.env.current_contract_address(), fee_recipient, platform_fee)
        if split_fee > U128(0):
            self.env.call(token_address, "transfer", self.env.current_contract_address(), split_recipient, split_fee)
        if owner_share > U128(0):
            self.env.call(token_address, "transfer", self.env.current_contract_address(), owner, owner_share)
