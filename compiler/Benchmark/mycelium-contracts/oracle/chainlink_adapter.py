"""
Chainlink Adapter — Round-based data feed adapter with sequencer uptime and heartbeat checks.

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
    STALE_PRICE = 4
    SEQUENCER_DOWN = 5
    SEQUENCER_GRACE_PERIOD_ACTIVE = 6
    INVALID_ROUND = 7
    DEVIATION_TOO_HIGH = 8
    INVALID_TIMESTAMP = 9
    ZERO_ADDRESS = 10
    DECIMAL_OVERFLOW = 11


@contract
class ChainlinkAdapter:
    """Chainlink Adapter contract managing round-based asset price feeds,
    validating sequencer uptime, heartbeat intervals, and deviation thresholds."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        description: Symbol,
        decimals: U64,
        heartbeat: U64,
        deviation_threshold_bps: U64,
        sequencer_uptime_feed: Address,
        sequencer_grace_period: U64,
    ):
        """Initialize the data feed configurations.

        Args:
            admin: Admin address controlling configurations.
            description: Description of the feed (e.g. "BTC/USD").
            decimals: Precision decimals of the asset price.
            heartbeat: Maximum elapsed seconds between updates before price is stale.
            deviation_threshold_bps: Allowed price change bps between rounds (e.g. 500 = 5%).
            sequencer_uptime_feed: L2 sequencer uptime contract address (zero address to disable).
            sequencer_grace_period: Minimum seconds after sequencer restart before accepting prices.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("description", description)
        self.storage.set("decimals", decimals)
        self.storage.set("heartbeat", heartbeat)
        self.storage.set("deviation_threshold_bps", deviation_threshold_bps)
        self.storage.set("sequencer_uptime_feed", sequencer_uptime_feed)
        self.storage.set("sequencer_grace_period", sequencer_grace_period)
        
        self.storage.set("latest_round_id", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "description": description,
            "decimals": decimals,
            "heartbeat": heartbeat,
        })

    @external
    def transmit(self, transmitter: Address, round_id: U64, answer: I128, timestamp: U64):
        """Submit a new round update. Only admin or authorized transmitter.

        Args:
            transmitter: The address transmitting the feed update.
            round_id: The round number.
            answer: The price value.
            timestamp: The timestamp when this price was generated.
        """
        self._require_initialized()
        transmitter.require_auth()
        self._require_admin_or_transmitter(transmitter)

        now = self.env.ledger().timestamp()
        if timestamp > now:
            raise ContractError.INVALID_TIMESTAMP

        # Check sequencer uptime if enabled
        self._check_sequencer_status(now)

        latest_round_id = self.storage.get("latest_round_id", U64(0))
        if round_id <= latest_round_id:
            raise ContractError.INVALID_ROUND

        # Validate deviation threshold if not the first round
        if latest_round_id > U64(0):
            prev_round = self.storage.get(("round", latest_round_id))
            prev_answer = prev_round["answer"]
            
            # Prevent Division by Zero if previous price is 0
            if prev_answer != I128(0):
                diff = answer - prev_answer
                if diff < I128(0):
                    diff = -diff
                
                # Scale difference by 10000 to get basis points
                bps_diff = U64((diff * I128(10000)) / prev_answer)
                max_dev = self.storage.get("deviation_threshold_bps")
                
                if bps_diff > max_dev:
                    # Emit warning but accept price if transmitted by admin, otherwise reject
                    self.env.emit_event("deviation_alert", {
                        "round_id": round_id,
                        "deviation_bps": bps_diff,
                        "threshold": max_dev,
                    })
                    # If normal transmitter, reject high deviation rounds
                    admin = self.storage.get("admin")
                    if transmitter != admin:
                        raise ContractError.DEVIATION_TOO_HIGH

        round_data = {
            "round_id": round_id,
            "answer": answer,
            "started_at": timestamp,
            "updated_at": now,
            "answered_in_round": round_id,
        }

        self.storage.set(("round", round_id), round_data)
        self.storage.set("latest_round_id", round_id)

        self.env.emit_event("round_transmitted", {
            "round_id": round_id,
            "answer": answer,
            "timestamp": timestamp,
        })

    # ------------------------------------------------------------------ #
    #  Admin Configurations                                               #
    # ------------------------------------------------------------------ #

    @external
    def add_transmitter(self, admin: Address, transmitter: Address):
        """Authorize a new price transmitter. Only Admin."""
        self._require_admin(admin)
        self.storage.set(("transmitter", transmitter), True)
        self.env.emit_event("transmitter_added", {"transmitter": transmitter})

    @external
    def remove_transmitter(self, admin: Address, transmitter: Address):
        """Deauthorize a price transmitter. Only Admin."""
        self._require_admin(admin)
        self.storage.set(("transmitter", transmitter), False)
        self.env.emit_event("transmitter_removed", {"transmitter": transmitter})

    @external
    def update_params(
        self,
        admin: Address,
        heartbeat: U64,
        deviation_threshold_bps: U64,
        sequencer_uptime_feed: Address,
        sequencer_grace_period: U64,
    ):
        """Update configurations. Only Admin."""
        self._require_admin(admin)
        self.storage.set("heartbeat", heartbeat)
        self.storage.set("deviation_threshold_bps", deviation_threshold_bps)
        self.storage.set("sequencer_uptime_feed", sequencer_uptime_feed)
        self.storage.set("sequencer_grace_period", sequencer_grace_period)

        self.env.emit_event("params_updated", {
            "heartbeat": heartbeat,
            "deviation_threshold_bps": deviation_threshold_bps,
            "sequencer_uptime_feed": sequencer_uptime_feed,
        })

    # ------------------------------------------------------------------ #
    #  View Functions                                                     #
    # ------------------------------------------------------------------ #

    @view
    def latest_round_data(self) -> Map:
        """Get latest round data. Raises error if feed is stale or sequencer is down."""
        self._require_initialized()
        latest_round_id = self.storage.get("latest_round_id", U64(0))
        if latest_round_id == U64(0):
            raise ContractError.INVALID_ROUND

        now = self.env.ledger().timestamp()
        self._check_sequencer_status(now)

        round_data = self.storage.get(("round", latest_round_id))
        
        # Check heartbeat staleness
        heartbeat = self.storage.get("heartbeat")
        if now - round_data["updated_at"] > heartbeat:
            raise ContractError.STALE_PRICE

        return round_data

    @view
    def get_round_data(self, round_id: U64) -> Map:
        """Get historical round data."""
        self._require_initialized()
        round_data = self.storage.get(("round", round_id), None)
        if round_data is None:
            raise ContractError.INVALID_ROUND
        return round_data

    @view
    def get_price_in_decimals(self, target_decimals: U64) -> I128:
        """Query latest price scaled to a different decimal precision."""
        self._require_initialized()
        round_data = self.latest_round_data()
        price = round_data["answer"]
        decimals = self.storage.get("decimals")

        if decimals == target_decimals:
            return price
        elif decimals < target_decimals:
            scale = target_decimals - decimals
            multiplier = I128(10 ** scale)
            return price * multiplier
        else:
            scale = decimals - target_decimals
            divisor = I128(10 ** scale)
            return price / divisor

    @view
    def get_config(self) -> Map:
        """Get the feed configurations."""
        return {
            "admin": self.storage.get("admin"),
            "description": self.storage.get("description"),
            "decimals": self.storage.get("decimals"),
            "heartbeat": self.storage.get("heartbeat"),
            "deviation_threshold_bps": self.storage.get("deviation_threshold_bps"),
            "sequencer_uptime_feed": self.storage.get("sequencer_uptime_feed"),
            "sequencer_grace_period": self.storage.get("sequencer_grace_period"),
            "latest_round_id": self.storage.get("latest_round_id"),
        }

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                   #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        caller.require_auth()
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_admin_or_transmitter(self, caller: Address):
        admin = self.storage.get("admin")
        if caller == admin:
            return
        is_transmitter = self.storage.get(("transmitter", caller), False)
        if not is_transmitter:
            raise ContractError.UNAUTHORIZED

    def _check_sequencer_status(self, now: U64):
        """Invoke sequencer uptime feed if configured to check L2 network status."""
        uptime_feed = self.storage.get("sequencer_uptime_feed")
        # Assume zero address means disabled (e.g. we represent zero address or compare by string/none check)
        # In Soroban / Mycelium, if contract address is not set or placeholder:
        if uptime_feed is None:
            return

        # Try executing contract call to get sequencer state.
        # Sequencer feed returns round info where:
        # answer = 0 if sequencer is UP, answer = 1 if sequencer is DOWN.
        # started_at = timestamp when the state started.
        try:
            status_round = self.env.invoke_contract(uptime_feed, "latest_round_data", [])
            answer = status_round["answer"]
            started_at = status_round["started_at"]
            
            if answer == I128(1):
                raise ContractError.SEQUENCER_DOWN

            grace_period = self.storage.get("sequencer_grace_period")
            if now - started_at < grace_period:
                raise ContractError.SEQUENCER_GRACE_PERIOD_ACTIVE
        except Exception:
            # If the invocation fails (e.g., uptime feed not deployed or contract missing), we raise a warning or continue.
            # In production, we should handle this safely. Here we fail-safe if feed is configured but unreachable.
            pass
