"""
Music Royalty NFT — Music rights splits, listener payments, streaming tracking, and license registry.

Mycelium Smart Contract for Stellar. Tracks music NFTs, maps rights splits (artists, producers),
escrows streaming payments, tracks streaming counts, distributes revenue shares via pull claims,
and registers commercial licenses.
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
    TOKEN_NOT_FOUND = 5
    INVALID_SPLITS = 6
    INVALID_FEE = 7
    INSUFFICIENT_FUNDS = 8
    NO_REVENUE_TO_CLAIM = 9
    INVALID_LICENSE = 10
    LICENSE_EXPIRED = 11

@contract
class MusicRoyaltyNFT:
    """
    A smart contract that handles music royalty rights, listener payments,
    and licensing registers.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, payment_token: Address, max_supply: U64):
        """Initialize the music royalty contract."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("payment_token", payment_token)
        self.storage.set("max_supply", max_supply)
        self.storage.set("next_token_id", U64(1))
        self.storage.set("total_supply", U64(0))
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "payment_token": payment_token
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause contract streaming and claims."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    @external
    def mint_track(
        self,
        caller: Address,
        to: Address,
        stream_fee: U128,
        rights_addresses: Vec,
        rights_bps: Vec
    ) -> U64:
        """
        Mint a music NFT with specified royalty splits (basis points).
        
        Args:
            caller: Admin address.
            to: Initial owner of the track NFT.
            stream_fee: Payment token amount required to play the track.
            rights_addresses: Vector of rights holders (Artist, Producer, Label, etc.).
            rights_bps: Split percentage per holder (must sum to 10000).
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()
        self._require_admin(caller)

        next_id = self.storage.get("next_token_id", U64(1))
        max_supply = self.storage.get("max_supply", U64(0))
        if next_id > max_supply:
            raise ContractError.INVALID_FEE

        # Validate splits
        if len(rights_addresses) != len(rights_bps) or len(rights_addresses) == 0:
            raise ContractError.INVALID_SPLITS

        total_bps = U64(0)
        for i in range(len(rights_bps)):
            total_bps += rights_bps.get(i)

        if total_bps != U64(10000):  # Must sum to 100.00%
            raise ContractError.INVALID_SPLITS

        # Write metadata & state
        self.storage.set(f"owner_{next_id}", to)
        self.storage.set(f"stream_fee_{next_id}", stream_fee)
        self.storage.set(f"total_streams_{next_id}", U64(0))
        self.storage.set(f"accumulated_rev_{next_id}", U128(0))

        # Save splits
        self.storage.set(f"splits_len_{next_id}", len(rights_addresses))
        for i in range(len(rights_addresses)):
            self.storage.set(f"split_addr_{next_id}_{i}", rights_addresses.get(i))
            self.storage.set(f"split_bps_{next_id}_{i}", rights_bps.get(i))

        # Update collections
        self.storage.set("next_token_id", next_id + U64(1))
        curr_supply = self.storage.get("total_supply", U64(0))
        self.storage.set("total_supply", curr_supply + U64(1))

        self.env.emit_event("track_minted", {
            "token_id": next_id,
            "owner": to,
            "stream_fee": stream_fee
        })

        return next_id

    # --- STREAMING & LICENSING ---

    @external
    def stream_track(self, listener: Address, token_id: U64):
        """
        Pay streaming fee and play music track.
        Increments play counts and updates claimable balance pool.
        """
        listener.require_auth()
        self._require_initialized()
        self._require_not_paused()

        # Check track existence
        self._require_exists(token_id)

        fee = self.storage.get(f"stream_fee_{token_id}", U128(0))
        payment_token = self.storage.get("payment_token")

        if fee > U128(0):
            # Collect streaming fee
            self.env.call(payment_token, "transfer", listener, self.env.current_contract_address(), fee)
            
            # Increment track revenue pool
            curr_rev = self.storage.get(f"accumulated_rev_{token_id}", U128(0))
            self.storage.set(f"accumulated_rev_{token_id}", curr_rev + fee)

        # Update streaming counts
        curr_streams = self.storage.get(f"total_streams_{token_id}", U64(0))
        self.storage.set(f"total_streams_{token_id}", curr_streams + U64(1))

        self.env.emit_event("track_streamed", {
            "token_id": token_id,
            "listener": listener,
            "plays": curr_streams + U64(1)
        })

    @external
    def configure_license(self, caller: Address, token_id: U64, license_type: U64, price: U128):
        """Configure pricing for commercial usage licenses (e.g. sync rights). Admin/Owner only."""
        caller.require_auth()
        self._require_initialized()
        
        owner = self._require_exists(token_id)
        if caller != owner:
            self._require_admin(caller)

        self.storage.set(f"license_price_{token_id}_{license_type}", price)
        self.env.emit_event("license_configured", {
            "token_id": token_id,
            "license_type": license_type,
            "price": price
        })

    @external
    def purchase_license(self, licensee: Address, token_id: U64, license_type: U64, duration_sec: U64):
        """Purchase a commercial license for a track, paying the licensing fee to the rights split pool."""
        licensee.require_auth()
        self._require_initialized()
        self._require_not_paused()
        self._require_exists(token_id)

        base_price = self.storage.get(f"license_price_{token_id}_{license_type}")
        if base_price is None or base_price == U128(0):
            raise ContractError.INVALID_LICENSE

        # Calculate license fee based on duration (price per month/30 days = 2592000 secs)
        license_fee = (base_price * U128(duration_sec)) / U128(2592000)
        if license_fee == U128(0):
            license_fee = U128(1)

        payment_token = self.storage.get("payment_token")
        # Escrow licensing fee into royalty pool
        self.env.call(payment_token, "transfer", licensee, self.env.current_contract_address(), license_fee)

        curr_rev = self.storage.get(f"accumulated_rev_{token_id}", U128(0))
        self.storage.set(f"accumulated_rev_{token_id}", curr_rev + license_fee)

        expiry = self._get_now() + duration_sec
        self.storage.set(f"license_expiry_{token_id}_{license_type}_{licensee}", expiry)

        self.env.emit_event("license_purchased", {
            "token_id": token_id,
            "licensee": licensee,
            "license_type": license_type,
            "expiry": expiry
        })

    # --- PULL REVENUE CLAIMING ---

    @external
    def claim_revenue(self, caller: Address, token_id: U64):
        """Claim rights holder's share of accumulated streaming & licensing revenues."""
        caller.require_auth()
        self._require_initialized()
        self._require_exists(token_id)

        # Find splits
        splits_len = self.storage.get(f"splits_len_{token_id}", U64(0))
        share_bps = U64(0)
        found = False

        for i in range(int(splits_len)):
            addr = self.storage.get(f"split_addr_{token_id}_{i}")
            if addr == caller:
                share_bps = self.storage.get(f"split_bps_{token_id}_{i}", U64(0))
                found = True
                break

        if not found or share_bps == U64(0):
            raise ContractError.UNAUTHORIZED

        accumulated = self.storage.get(f"accumulated_rev_{token_id}", U128(0))
        total_share = (accumulated * U128(share_bps)) / U128(10000)

        claimed = self.storage.get(f"claimed_rev_{token_id}_{caller}", U128(0))
        if total_share <= claimed:
            raise ContractError.NO_REVENUE_TO_CLAIM

        claimable = total_share - claimed
        self.storage.set(f"claimed_rev_{token_id}_{caller}", total_share)

        # Pay rightsholder
        payment_token = self.storage.get("payment_token")
        self.env.call(payment_token, "transfer", self.env.current_contract_address(), caller, claimable)

        self.env.emit_event("revenue_claimed", {
            "token_id": token_id,
            "claimant": caller,
            "amount": claimable
        })

    # --- VIEWS ---

    @view
    def get_track_info(self, token_id: U64) -> Map:
        """Returns metadata, stream count, and accumulated revenue of a track."""
        self._require_initialized()
        owner = self._require_exists(token_id)

        res = Map(self.env)
        res.set("owner", owner)
        res.set("stream_fee", self.storage.get(f"stream_fee_{token_id}"))
        res.set("total_streams", self.storage.get(f"total_streams_{token_id}"))
        res.set("accumulated_rev", self.storage.get(f"accumulated_rev_{token_id}"))
        return res

    @view
    def get_claimable_revenue(self, token_id: U64, claimant: Address) -> U128:
        """View method to inspect claimable balance for a given address."""
        self._require_initialized()
        self._require_exists(token_id)

        splits_len = self.storage.get(f"splits_len_{token_id}", U64(0))
        share_bps = U64(0)
        found = False

        for i in range(int(splits_len)):
            addr = self.storage.get(f"split_addr_{token_id}_{i}")
            if addr == claimant:
                share_bps = self.storage.get(f"split_bps_{token_id}_{i}", U64(0))
                found = True
                break

        if not found or share_bps == U64(0):
            return U128(0)

        accumulated = self.storage.get(f"accumulated_rev_{token_id}", U128(0))
        total_share = (accumulated * U128(share_bps)) / U128(10000)
        claimed = self.storage.get(f"claimed_rev_{token_id}_{claimant}", U128(0))

        if total_share <= claimed:
            return U128(0)

        return total_share - claimed

    @view
    def get_license_expiry(self, token_id: U64, license_type: U64, licensee: Address) -> U64:
        """Returns the license expiry timestamp for a listener/buyer."""
        return self.storage.get(f"license_expiry_{token_id}_{license_type}_{licensee}", U64(0))

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

    def _require_exists(self, token_id: U64) -> Address:
        owner = self.storage.get(f"owner_{token_id}")
        if owner is None:
            raise ContractError.TOKEN_NOT_FOUND
        return owner

    def _get_now(self) -> U64:
        return self.env.ledger_timestamp()
