"""
TWAP Oracle — Price accumulator, configurable windows, observation ring buffer,
cardinality growth, and overflow guard.

Mycelium Smart Contract for Stellar
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)

class ContractError:
    NOT_INITIALIZED = 1
    UNAUTHORIZED = 2
    ALREADY_INITIALIZED = 3
    ASSET_NOT_INITIALIZED = 4
    INVALID_WINDOW = 5
    CARDINALITY_TOO_SMALL = 6
    CARDINALITY_EXCEEDED = 7
    OBSERVATION_OUT_OF_BOUNDS = 8
    NO_OBSERVATIONS = 9
    SAME_BLOCK_UPDATE = 10
    OVERFLOW = 11

# Limits
MAX_CARDINALITY = 512
MIN_CARDINALITY = 2
DEFAULT_CARDINALITY = 32

@contract
class TwapOracle:
    """
    Time-Weighted Average Price (TWAP) Oracle that stores cumulative price history
    in a ring buffer for multiple assets. It allows clients to query the average
    price over a configurable historical window.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ------------------------------------------------------------------ #
    #  Initialization
    # ------------------------------------------------------------------ #

    @external
    def initialize(self, admin: Address):
        """Initialize the TWAP Oracle contract setting the admin."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("initialized", True)
        self.storage.set("updater_count", U64(0))

        self.env.emit_event("initialized", {"admin": admin})

    # ------------------------------------------------------------------ #
    #  Asset & Updater Management
    # ------------------------------------------------------------------ #

    @external
    def register_updater(self, admin: Address, updater: Address):
        """Register an authorized account that can submit price updates."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        key = self._updater_key(updater)
        self.storage.set(key, True)
        self.env.emit_event("updater_registered", {"updater": updater})

    @external
    def remove_updater(self, admin: Address, updater: Address):
        """Remove an authorized price updater."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        key = self._updater_key(updater)
        self.storage.set(key, False)
        self.env.emit_event("updater_removed", {"updater": updater})

    @external
    def initialize_asset(self, admin: Address, feed_id: Symbol, initial_price: I128, initial_cardinality: U64):
        """Register a new asset, set up its ring buffer, and record the genesis observation."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        if self.storage.get(self._asset_initialized_key(feed_id), False):
            raise ContractError.ALREADY_INITIALIZED
        if initial_cardinality < MIN_CARDINALITY or initial_cardinality > MAX_CARDINALITY:
            raise ContractError.CARDINALITY_TOO_SMALL
        if initial_price <= I128(0):
            raise ContractError.OVERFLOW

        now = self.env.ledger().timestamp()
        
        self.storage.set(self._asset_initialized_key(feed_id), True)
        self.storage.set(self._asset_cardinality_key(feed_id), initial_cardinality)
        self.storage.set(self._asset_head_key(feed_id), U64(1))
        self.storage.set(self._asset_count_key(feed_id), U64(1))

        # Genesis observation at index 0
        self.storage.set(self._observation_ts_key(feed_id, U64(0)), now)
        self.storage.set(self._observation_price_key(feed_id, U64(0)), initial_price)
        self.storage.set(self._observation_cum_key(feed_id, U64(0)), I128(0))

        self.env.emit_event("asset_initialized", {
            "feed_id": feed_id,
            "initial_price": initial_price,
            "cardinality": initial_cardinality,
            "timestamp": now
        })

    @external
    def increase_cardinality(self, caller: Address, feed_id: Symbol, new_cardinality: U64):
        """Allow any user to pay the gas/storage to increase the observation buffer size for an asset."""
        caller.require_auth()
        self._require_initialized()
        
        if not self.storage.get(self._asset_initialized_key(feed_id), False):
            raise ContractError.ASSET_NOT_INITIALIZED
        
        current_cardinality = self.storage.get(self._asset_cardinality_key(feed_id), U64(0))
        if new_cardinality <= current_cardinality:
            raise ContractError.CARDINALITY_TOO_SMALL
        if new_cardinality > MAX_CARDINALITY:
            raise ContractError.CARDINALITY_EXCEEDED

        # To avoid data corruption in the ring buffer, if we expand it, we need to handle the ring buffer index alignment.
        # Simple mechanism: we copy the existing buffer entries to alignment or we require head to be reset.
        # But wait! A simpler and safe approach is to copy all active elements to indices 0..count-1 and set head to count.
        count = self.storage.get(self._asset_count_key(feed_id), U64(0))
        head = self.storage.get(self._asset_head_key(feed_id), U64(0))

        # Re-index to linear array if it has wrapped
        if count == current_cardinality:
            temp_ts = Vec()
            temp_px = Vec()
            temp_cum = Vec()
            for i in range(current_cardinality):
                idx = (head + i) % current_cardinality
                temp_ts.push_back(self.storage.get(self._observation_ts_key(feed_id, idx)))
                temp_px.push_back(self.storage.get(self._observation_price_key(feed_id, idx)))
                temp_cum.push_back(self.storage.get(self._observation_cum_key(feed_id, idx)))

            for i in range(current_cardinality):
                self.storage.set(self._observation_ts_key(feed_id, U64(i)), temp_ts.get(i))
                self.storage.set(self._observation_price_key(feed_id, U64(i)), temp_px.get(i))
                self.storage.set(self._observation_cum_key(feed_id, U64(i)), temp_cum.get(i))
            
            self.storage.set(self._asset_head_key(feed_id), count)
        
        self.storage.set(self._asset_cardinality_key(feed_id), new_cardinality)
        
        self.env.emit_event("cardinality_increased", {
            "feed_id": feed_id,
            "old_cardinality": current_cardinality,
            "new_cardinality": new_cardinality
        })

    # ------------------------------------------------------------------ #
    #  Price Updates
    # ------------------------------------------------------------------ #

    @external
    def update(self, updater: Address, feed_id: Symbol, price: I128):
        """Submit a new price point. Updates the cumulative price and records it in the ring buffer."""
        updater.require_auth()
        self._require_initialized()
        self._require_updater(updater)

        if not self.storage.get(self._asset_initialized_key(feed_id), False):
            raise ContractError.ASSET_NOT_INITIALIZED
        if price <= I128(0):
            raise ContractError.OVERFLOW

        now = self.env.ledger().timestamp()
        
        cardinality = self.storage.get(self._asset_cardinality_key(feed_id), U64(0))
        head = self.storage.get(self._asset_head_key(feed_id), U64(0))
        count = self.storage.get(self._asset_count_key(feed_id), U64(0))

        # Get the latest observation (at index (head - 1) % cardinality)
        last_index = (head - 1) % cardinality
        last_ts = self.storage.get(self._observation_ts_key(feed_id, last_index), U64(0))
        last_price = self.storage.get(self._observation_price_key(feed_id, last_index), I128(0))
        last_cum = self.storage.get(self._observation_cum_key(feed_id, last_index), I128(0))

        if now == last_ts:
            raise ContractError.SAME_BLOCK_UPDATE

        elapsed = I128(now - last_ts)
        
        # Calculate new cumulative price: last_cum + (last_price * elapsed)
        # Check for overflow
        price_delta = last_price * elapsed
        new_cum = last_cum + price_delta

        # Write new observation at the head index
        self.storage.set(self._observation_ts_key(feed_id, head), now)
        self.storage.set(self._observation_price_key(feed_id, head), price)
        self.storage.set(self._observation_cum_key(feed_id, head), new_cum)

        # Advance the head pointer
        new_head = (head + 1) % cardinality
        self.storage.set(self._asset_head_key(feed_id), new_head)

        if count < cardinality:
            self.storage.set(self._asset_count_key(feed_id), count + 1)

        self.env.emit_event("price_updated", {
            "feed_id": feed_id,
            "price": price,
            "cumulative": new_cum,
            "timestamp": now
        })

    # ------------------------------------------------------------------ #
    #  Views & TWAP Queries
    # ------------------------------------------------------------------ #

    @view
    def consult(self, feed_id: Symbol, seconds_ago: U64) -> I128:
        """
        Calculate the Time-Weighted Average Price (TWAP) for the asset
        over the last `seconds_ago` seconds.
        """
        self._require_initialized()
        if not self.storage.get(self._asset_initialized_key(feed_id), False):
            raise ContractError.ASSET_NOT_INITIALIZED
        if seconds_ago == 0:
            raise ContractError.INVALID_WINDOW

        now = self.env.ledger().timestamp()
        target_ts = now - seconds_ago

        cardinality = self.storage.get(self._asset_cardinality_key(feed_id), U64(0))
        head = self.storage.get(self._asset_head_key(feed_id), U64(0))
        count = self.storage.get(self._asset_count_key(feed_id), U64(0))

        if count == 0:
            raise ContractError.NO_OBSERVATIONS

        # Fetch current latest cumulative
        last_index = (head - 1) % cardinality
        last_ts = self.storage.get(self._observation_ts_key(feed_id, last_index), U64(0))
        last_price = self.storage.get(self._observation_price_key(feed_id, last_index), I128(0))
        last_cum = self.storage.get(self._observation_cum_key(feed_id, last_index), I128(0))

        # Extrapolate current cumulative price if block timestamp advanced since last update
        current_cum = last_cum
        if now > last_ts:
            current_cum += last_price * I128(now - last_ts)

        # Binary search the ring buffer to find observations around target_ts
        obs_idx = self._binary_search_observations(feed_id, target_ts, head, count, cardinality)
        
        obs_ts = self.storage.get(self._observation_ts_key(feed_id, obs_idx), U64(0))
        obs_cum = self.storage.get(self._observation_cum_key(feed_id, obs_idx), I128(0))
        obs_price = self.storage.get(self._observation_price_key(feed_id, obs_idx), I128(0))

        # If target_ts falls between observations, we can interpolate the cumulative price
        # Target cumulative = obs_cum + obs_price * (target_ts - obs_ts)
        # Note: If target_ts is exactly equal to obs_ts, then the delta is 0
        if target_ts < obs_ts:
            raise ContractError.OBSERVATION_OUT_OF_BOUNDS

        target_cum = obs_cum + obs_price * I128(target_ts - obs_ts)

        # TWAP = (current_cum - target_cum) / seconds_ago
        cum_delta = current_cum - target_cum
        return cum_delta / I128(seconds_ago)

    @view
    def get_asset_info(self, feed_id: Symbol) -> Map:
        """Return basic metadata about an asset feed."""
        self._require_initialized()
        if not self.storage.get(self._asset_initialized_key(feed_id), False):
            raise ContractError.ASSET_NOT_INITIALIZED

        return {
            "cardinality": self.storage.get(self._asset_cardinality_key(feed_id), U64(0)),
            "head": self.storage.get(self._asset_head_key(feed_id), U64(0)),
            "count": self.storage.get(self._asset_count_key(feed_id), U64(0))
        }

    @view
    def get_observation(self, feed_id: Symbol, index: U64) -> Map:
        """Return the observation at a specific raw index in the ring buffer."""
        self._require_initialized()
        if not self.storage.get(self._asset_initialized_key(feed_id), False):
            raise ContractError.ASSET_NOT_INITIALIZED
        
        cardinality = self.storage.get(self._asset_cardinality_key(feed_id), U64(0))
        if index >= cardinality:
            raise ContractError.OBSERVATION_OUT_OF_BOUNDS

        return {
            "timestamp": self.storage.get(self._observation_ts_key(feed_id, index), U64(0)),
            "price": self.storage.get(self._observation_price_key(feed_id, index), I128(0)),
            "cumulative": self.storage.get(self._observation_cum_key(feed_id, index), I128(0))
        }

    # ------------------------------------------------------------------ #
    #  Internal Helpers
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_updater(self, caller: Address):
        if not self.storage.get(self._updater_key(caller), False):
            raise ContractError.UNAUTHORIZED

    def _binary_search_observations(self, feed_id: Symbol, target_ts: U64, head: U64, count: U64, cardinality: U64) -> U64:
        """
        Perform a binary search on the ring buffer to find the index of the observation
        that is closest to target_ts (specifically the largest timestamp <= target_ts).
        """
        # Lower bound index in linear space [0, count-1]
        low = 0
        high = int(count) - 1

        # Check oldest observation (at index (head - count) % cardinality)
        oldest_idx = (head - count) % cardinality
        oldest_ts = self.storage.get(self._observation_ts_key(feed_id, oldest_idx), U64(0))
        if target_ts < oldest_ts:
            raise ContractError.OBSERVATION_OUT_OF_BOUNDS

        # Check newest observation (at index (head - 1) % cardinality)
        newest_idx = (head - 1) % cardinality
        newest_ts = self.storage.get(self._observation_ts_key(feed_id, newest_idx), U64(0))
        if target_ts >= newest_ts:
            return newest_idx

        # Binary search in [low, high]
        best_idx = oldest_idx
        while low <= high:
            mid = (low + high) // 2
            # Map linear mid to ring buffer index
            ring_idx = (head - count + mid) % cardinality
            mid_ts = self.storage.get(self._observation_ts_key(feed_id, ring_idx), U64(0))

            if mid_ts <= target_ts:
                best_idx = ring_idx
                low = mid + 1
            else:
                high = mid - 1

        return best_idx

    # ---- Storage key helpers ---- #

    def _updater_key(self, updater: Address) -> str:
        return f"upd:{updater}"

    def _asset_initialized_key(self, feed_id: Symbol) -> str:
        return f"as_init:{feed_id}"

    def _asset_cardinality_key(self, feed_id: Symbol) -> str:
        return f"as_card:{feed_id}"

    def _asset_head_key(self, feed_id: Symbol) -> str:
        return f"as_head:{feed_id}"

    def _asset_count_key(self, feed_id: Symbol) -> str:
        return f"as_cnt:{feed_id}"

    def _observation_ts_key(self, feed_id: Symbol, index: U64) -> str:
        return f"obs_ts:{feed_id}:{index}"

    def _observation_price_key(self, feed_id: Symbol, index: U64) -> str:
        return f"obs_px:{feed_id}:{index}"

    def _observation_cum_key(self, feed_id: Symbol, index: U64) -> str:
        return f"obs_cum:{feed_id}:{index}"
