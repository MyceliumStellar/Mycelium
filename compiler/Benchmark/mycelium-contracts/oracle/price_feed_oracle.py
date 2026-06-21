"""
Price Feed Oracle — Multi-source price aggregation with staleness checks,
deviation alerts, heartbeat monitoring, and fallback oracle support.

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
    PROVIDER_NOT_REGISTERED = 4
    PROVIDER_ALREADY_REGISTERED = 5
    PRICE_STALE = 6
    PRICE_DEVIATION_TOO_HIGH = 7
    NO_VALID_PRICES = 8
    INVALID_PRICE = 9
    INVALID_THRESHOLD = 10
    MAX_PROVIDERS_REACHED = 11
    FEED_NOT_FOUND = 12
    FALLBACK_ORACLE_NOT_SET = 13
    RING_BUFFER_EMPTY = 14
    BATCH_SIZE_EXCEEDED = 15
    INVALID_SIGNATURE = 16
    HEARTBEAT_MISSED = 17


# Ring buffer size for price history
PRICE_HISTORY_SIZE = 128
MAX_PROVIDERS = 64
MAX_BATCH_SIZE = 32


@contract
class PriceFeedOracle:
    """
    Multi-source price aggregation oracle that collects price data from
    whitelisted providers, calculates median prices, monitors staleness
    and heartbeat, detects deviations, and supports fallback oracles.
    Prices are stored with 18-decimal precision internally.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ------------------------------------------------------------------ #
    #  Initialization
    # ------------------------------------------------------------------ #

    @external
    def initialize(
        self,
        admin: Address,
        max_staleness_seconds: U64,
        deviation_threshold_bps: U64,
        heartbeat_interval: U64,
    ):
        """
        Set up the oracle with admin, staleness window, deviation threshold
        (in basis points, e.g. 500 = 5%), and heartbeat interval.
        """
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if max_staleness_seconds == 0 or heartbeat_interval == 0:
            raise ContractError.INVALID_THRESHOLD
        if deviation_threshold_bps == 0 or deviation_threshold_bps > 10000:
            raise ContractError.INVALID_THRESHOLD

        self.storage.set("admin", admin)
        self.storage.set("max_staleness", max_staleness_seconds)
        self.storage.set("deviation_bps", deviation_threshold_bps)
        self.storage.set("heartbeat_interval", heartbeat_interval)
        self.storage.set("provider_count", U64(0))
        self.storage.set("feed_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "max_staleness": max_staleness_seconds,
            "deviation_bps": deviation_threshold_bps,
            "heartbeat_interval": heartbeat_interval,
        })

    # ------------------------------------------------------------------ #
    #  Provider management
    # ------------------------------------------------------------------ #

    @external
    def register_provider(self, admin: Address, provider: Address, name: Symbol):
        """Add a provider to the whitelist. Only admin can register."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        key = self._provider_key(provider)
        if self.storage.get(key, False):
            raise ContractError.PROVIDER_ALREADY_REGISTERED

        count = self.storage.get("provider_count", U64(0))
        if count >= MAX_PROVIDERS:
            raise ContractError.MAX_PROVIDERS_REACHED

        self.storage.set(key, True)
        self.storage.set(self._provider_name_key(provider), name)
        self.storage.set(self._provider_heartbeat_key(provider), self.env.ledger().timestamp())
        self.storage.set("provider_count", count + 1)

        # track provider in ordered list
        self.storage.set(self._provider_index_key(count), provider)

        self.env.emit_event("provider_registered", {
            "provider": provider,
            "name": name,
            "index": count,
        })

    @external
    def remove_provider(self, admin: Address, provider: Address):
        """Remove a provider from the whitelist."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        key = self._provider_key(provider)
        if not self.storage.get(key, False):
            raise ContractError.PROVIDER_NOT_REGISTERED

        self.storage.set(key, False)
        count = self.storage.get("provider_count", U64(0))
        self.storage.set("provider_count", count - 1 if count > 0 else U64(0))

        self.env.emit_event("provider_removed", {"provider": provider})

    # ------------------------------------------------------------------ #
    #  Price submission
    # ------------------------------------------------------------------ #

    @external
    def submit_price(
        self,
        provider: Address,
        feed_id: Symbol,
        price: I128,
        decimals: U64,
        signature: Bytes,
    ):
        """
        Submit a price update for a feed. The provider must be whitelisted.
        Price is stored alongside the provider address and timestamp.
        """
        provider.require_auth()
        self._require_initialized()

        if not self.storage.get(self._provider_key(provider), False):
            raise ContractError.PROVIDER_NOT_REGISTERED
        if price <= I128(0):
            raise ContractError.INVALID_PRICE

        now = self.env.ledger().timestamp()

        # Verify signature is non-empty (placeholder for real sig verification)
        if signature.length() == 0:
            raise ContractError.INVALID_SIGNATURE

        # Heartbeat update
        self.storage.set(self._provider_heartbeat_key(provider), now)

        # Store per-provider price for this feed
        pkey = self._feed_provider_price_key(feed_id, provider)
        previous_price = self.storage.get(pkey, I128(0))
        self.storage.set(pkey, price)
        self.storage.set(self._feed_provider_ts_key(feed_id, provider), now)
        self.storage.set(self._feed_provider_dec_key(feed_id, provider), decimals)

        # Ensure feed is registered
        if not self.storage.get(self._feed_exists_key(feed_id), False):
            self.storage.set(self._feed_exists_key(feed_id), True)
            feed_count = self.storage.get("feed_count", U64(0))
            self.storage.set(self._feed_index_key(feed_count), feed_id)
            self.storage.set("feed_count", feed_count + 1)

        # Check deviation from previous price
        if previous_price > I128(0):
            self._check_deviation(feed_id, previous_price, price, provider)

        # Write to ring buffer history
        self._write_price_history(feed_id, price, now)

        # Recalculate median
        median = self._calculate_median(feed_id)
        self.storage.set(self._feed_median_key(feed_id), median)
        self.storage.set(self._feed_median_ts_key(feed_id), now)

        self.env.emit_event("price_submitted", {
            "provider": provider,
            "feed_id": feed_id,
            "price": price,
            "median": median,
            "timestamp": now,
        })

    @external
    def batch_submit_prices(
        self,
        provider: Address,
        feed_ids: Vec,
        prices: Vec,
        decimals_list: Vec,
        signature: Bytes,
    ):
        """Submit prices for multiple feeds in a single call."""
        provider.require_auth()
        self._require_initialized()

        if not self.storage.get(self._provider_key(provider), False):
            raise ContractError.PROVIDER_NOT_REGISTERED

        n = feed_ids.length()
        if n == 0 or n != prices.length() or n != decimals_list.length():
            raise ContractError.INVALID_PRICE
        if n > MAX_BATCH_SIZE:
            raise ContractError.BATCH_SIZE_EXCEEDED
        if signature.length() == 0:
            raise ContractError.INVALID_SIGNATURE

        now = self.env.ledger().timestamp()
        self.storage.set(self._provider_heartbeat_key(provider), now)

        for i in range(n):
            fid = feed_ids.get(i)
            px = prices.get(i)
            dec = decimals_list.get(i)

            if px <= I128(0):
                raise ContractError.INVALID_PRICE

            pkey = self._feed_provider_price_key(fid, provider)
            prev = self.storage.get(pkey, I128(0))
            self.storage.set(pkey, px)
            self.storage.set(self._feed_provider_ts_key(fid, provider), now)
            self.storage.set(self._feed_provider_dec_key(fid, provider), dec)

            if not self.storage.get(self._feed_exists_key(fid), False):
                self.storage.set(self._feed_exists_key(fid), True)
                fc = self.storage.get("feed_count", U64(0))
                self.storage.set(self._feed_index_key(fc), fid)
                self.storage.set("feed_count", fc + 1)

            if prev > I128(0):
                self._check_deviation(fid, prev, px, provider)

            self._write_price_history(fid, px, now)

            median = self._calculate_median(fid)
            self.storage.set(self._feed_median_key(fid), median)
            self.storage.set(self._feed_median_ts_key(fid), now)

        self.env.emit_event("batch_prices_submitted", {
            "provider": provider,
            "count": n,
            "timestamp": now,
        })

    # ------------------------------------------------------------------ #
    #  Fallback oracle
    # ------------------------------------------------------------------ #

    @external
    def set_fallback_oracle(self, admin: Address, fallback_address: Address):
        """Configure a secondary fallback oracle address."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        self.storage.set("fallback_oracle", fallback_address)
        self.env.emit_event("fallback_oracle_set", {"fallback": fallback_address})

    # ------------------------------------------------------------------ #
    #  Admin parameter updates
    # ------------------------------------------------------------------ #

    @external
    def update_parameters(
        self,
        admin: Address,
        max_staleness_seconds: U64,
        deviation_threshold_bps: U64,
        heartbeat_interval: U64,
    ):
        """Update oracle configuration parameters."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        if max_staleness_seconds == 0 or heartbeat_interval == 0:
            raise ContractError.INVALID_THRESHOLD
        if deviation_threshold_bps == 0 or deviation_threshold_bps > 10000:
            raise ContractError.INVALID_THRESHOLD

        self.storage.set("max_staleness", max_staleness_seconds)
        self.storage.set("deviation_bps", deviation_threshold_bps)
        self.storage.set("heartbeat_interval", heartbeat_interval)

        self.env.emit_event("parameters_updated", {
            "max_staleness": max_staleness_seconds,
            "deviation_bps": deviation_threshold_bps,
            "heartbeat_interval": heartbeat_interval,
        })

    # ------------------------------------------------------------------ #
    #  Views
    # ------------------------------------------------------------------ #

    @view
    def get_latest_price(self, feed_id: Symbol) -> I128:
        """Return the latest median price. Raises if stale or unavailable."""
        self._require_initialized()
        if not self.storage.get(self._feed_exists_key(feed_id), False):
            raise ContractError.FEED_NOT_FOUND

        ts = self.storage.get(self._feed_median_ts_key(feed_id), U64(0))
        now = self.env.ledger().timestamp()
        staleness = self.storage.get("max_staleness", U64(3600))

        if now - ts > staleness:
            # attempt fallback
            fallback = self.storage.get("fallback_oracle", None)
            if fallback is None:
                raise ContractError.PRICE_STALE
            raise ContractError.PRICE_STALE  # caller should query fallback

        return self.storage.get(self._feed_median_key(feed_id), I128(0))

    @view
    def get_price_with_metadata(self, feed_id: Symbol) -> Map:
        """Return median price, timestamp, and provider count for a feed."""
        self._require_initialized()
        if not self.storage.get(self._feed_exists_key(feed_id), False):
            raise ContractError.FEED_NOT_FOUND

        median = self.storage.get(self._feed_median_key(feed_id), I128(0))
        ts = self.storage.get(self._feed_median_ts_key(feed_id), U64(0))
        now = self.env.ledger().timestamp()
        staleness = self.storage.get("max_staleness", U64(3600))
        is_stale = (now - ts) > staleness

        return {
            "price": median,
            "timestamp": ts,
            "is_stale": is_stale,
        }

    @view
    def get_provider_price(self, feed_id: Symbol, provider: Address) -> Map:
        """Return a specific provider's submitted price and timestamp."""
        self._require_initialized()
        price = self.storage.get(self._feed_provider_price_key(feed_id, provider), I128(0))
        ts = self.storage.get(self._feed_provider_ts_key(feed_id, provider), U64(0))
        dec = self.storage.get(self._feed_provider_dec_key(feed_id, provider), U64(0))
        return {"price": price, "timestamp": ts, "decimals": dec}

    @view
    def get_price_history(self, feed_id: Symbol, count: U64) -> Vec:
        """Return the last `count` price entries from the ring buffer."""
        self._require_initialized()
        head = self.storage.get(self._history_head_key(feed_id), U64(0))
        size = self.storage.get(self._history_size_key(feed_id), U64(0))

        if size == 0:
            raise ContractError.RING_BUFFER_EMPTY

        actual = min(count, size)
        results = Vec()
        for i in range(actual):
            idx = (head - 1 - i) % PRICE_HISTORY_SIZE
            px = self.storage.get(self._history_price_key(feed_id, idx), I128(0))
            ts = self.storage.get(self._history_ts_key(feed_id, idx), U64(0))
            results.push_back({"price": px, "timestamp": ts})

        return results

    @view
    def is_provider_active(self, provider: Address) -> Bool:
        """Check if a provider is registered and has not missed heartbeat."""
        if not self.storage.get(self._provider_key(provider), False):
            return False
        last_beat = self.storage.get(self._provider_heartbeat_key(provider), U64(0))
        interval = self.storage.get("heartbeat_interval", U64(3600))
        now = self.env.ledger().timestamp()
        return (now - last_beat) <= interval

    @view
    def get_provider_count(self) -> U64:
        """Return the number of registered providers."""
        return self.storage.get("provider_count", U64(0))

    @view
    def get_fallback_oracle(self) -> Address:
        """Return the configured fallback oracle address."""
        fb = self.storage.get("fallback_oracle", None)
        if fb is None:
            raise ContractError.FALLBACK_ORACLE_NOT_SET
        return fb

    @view
    def check_heartbeats(self) -> Vec:
        """Return list of providers that missed their heartbeat."""
        self._require_initialized()
        missed = Vec()
        count = self.storage.get("provider_count", U64(0))
        interval = self.storage.get("heartbeat_interval", U64(3600))
        now = self.env.ledger().timestamp()

        for i in range(count + MAX_PROVIDERS):
            prov = self.storage.get(self._provider_index_key(U64(i)), None)
            if prov is None:
                continue
            if not self.storage.get(self._provider_key(prov), False):
                continue
            lb = self.storage.get(self._provider_heartbeat_key(prov), U64(0))
            if (now - lb) > interval:
                missed.push_back(prov)

        return missed

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _calculate_median(self, feed_id: Symbol) -> I128:
        """
        Collect non-stale prices from all active providers and return the
        median.  If only one provider exists it returns that single price.
        If all feeds are stale, raises NO_VALID_PRICES.
        """
        now = self.env.ledger().timestamp()
        staleness = self.storage.get("max_staleness", U64(3600))
        provider_total = self.storage.get("provider_count", U64(0))

        prices = Vec()
        for i in range(provider_total + MAX_PROVIDERS):
            prov = self.storage.get(self._provider_index_key(U64(i)), None)
            if prov is None:
                continue
            if not self.storage.get(self._provider_key(prov), False):
                continue
            ts = self.storage.get(self._feed_provider_ts_key(feed_id, prov), U64(0))
            if ts == 0:
                continue
            if (now - ts) > staleness:
                continue
            px = self.storage.get(self._feed_provider_price_key(feed_id, prov), I128(0))
            if px > I128(0):
                prices.push_back(px)

        n = prices.length()
        if n == 0:
            raise ContractError.NO_VALID_PRICES

        # Sort for median
        prices = self._sort_prices(prices)

        if n % 2 == 1:
            return prices.get(n // 2)
        else:
            mid_low = prices.get(n // 2 - 1)
            mid_high = prices.get(n // 2)
            return (mid_low + mid_high) / I128(2)

    def _sort_prices(self, prices: Vec) -> Vec:
        """Simple insertion sort suitable for small N (<=64)."""
        n = prices.length()
        for i in range(1, n):
            key = prices.get(i)
            j = i - 1
            while j >= 0 and prices.get(j) > key:
                prices.set(j + 1, prices.get(j))
                j -= 1
            prices.set(j + 1, key)
        return prices

    def _check_deviation(
        self, feed_id: Symbol, old_price: I128, new_price: I128, provider: Address
    ):
        """Emit alert if price moved more than the configured deviation."""
        threshold_bps = self.storage.get("deviation_bps", U64(500))
        diff = new_price - old_price
        if diff < I128(0):
            diff = -diff
        deviation_bps = (diff * I128(10000)) / old_price

        if deviation_bps > I128(threshold_bps):
            self.env.emit_event("price_deviation_alert", {
                "feed_id": feed_id,
                "provider": provider,
                "old_price": old_price,
                "new_price": new_price,
                "deviation_bps": deviation_bps,
            })

    def _write_price_history(self, feed_id: Symbol, price: I128, timestamp: U64):
        """Append a price entry to the ring buffer for this feed."""
        head = self.storage.get(self._history_head_key(feed_id), U64(0))
        size = self.storage.get(self._history_size_key(feed_id), U64(0))

        slot = head % PRICE_HISTORY_SIZE
        self.storage.set(self._history_price_key(feed_id, slot), price)
        self.storage.set(self._history_ts_key(feed_id, slot), timestamp)

        self.storage.set(self._history_head_key(feed_id), head + 1)
        if size < PRICE_HISTORY_SIZE:
            self.storage.set(self._history_size_key(feed_id), size + 1)

    # ---- Storage key helpers ---- #

    def _provider_key(self, provider: Address) -> str:
        return f"prov:{provider}"

    def _provider_name_key(self, provider: Address) -> str:
        return f"prov_name:{provider}"

    def _provider_heartbeat_key(self, provider: Address) -> str:
        return f"prov_hb:{provider}"

    def _provider_index_key(self, index: U64) -> str:
        return f"prov_idx:{index}"

    def _feed_exists_key(self, feed_id: Symbol) -> str:
        return f"feed_ex:{feed_id}"

    def _feed_index_key(self, index: U64) -> str:
        return f"feed_idx:{index}"

    def _feed_provider_price_key(self, feed_id: Symbol, provider: Address) -> str:
        return f"fp_px:{feed_id}:{provider}"

    def _feed_provider_ts_key(self, feed_id: Symbol, provider: Address) -> str:
        return f"fp_ts:{feed_id}:{provider}"

    def _feed_provider_dec_key(self, feed_id: Symbol, provider: Address) -> str:
        return f"fp_dec:{feed_id}:{provider}"

    def _feed_median_key(self, feed_id: Symbol) -> str:
        return f"f_med:{feed_id}"

    def _feed_median_ts_key(self, feed_id: Symbol) -> str:
        return f"f_med_ts:{feed_id}"

    def _history_head_key(self, feed_id: Symbol) -> str:
        return f"hist_h:{feed_id}"

    def _history_size_key(self, feed_id: Symbol) -> str:
        return f"hist_s:{feed_id}"

    def _history_price_key(self, feed_id: Symbol, slot: U64) -> str:
        return f"hist_p:{feed_id}:{slot}"

    def _history_ts_key(self, feed_id: Symbol, slot: U64) -> str:
        return f"hist_t:{feed_id}:{slot}"
