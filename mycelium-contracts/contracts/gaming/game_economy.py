"""
Game Economy System — Dual token faucets/sinks, bot pattern checks, inflation decay, and stabilization policies.

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
    CLAIM_RATE_LIMIT = 5
    BOT_PATTERN_DETECTED = 6
    INVALID_RATIO = 7
    INVALID_MULTIPLIER = 8
    SINK_NOT_FOUND = 9


@contract
class GameEconomySystem:
    """Controls gaming dual-token supply dynamics, faucets, sink adjustments, bot filters, and economic stabilization."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        gold_token: Address,
        gem_token: Address,
        oracle_address: Address
    ):
        """Initialize the Game Economy contract."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("gold_token", gold_token)
        self.storage.set("gem_token", gem_token)
        self.storage.set("oracle_address", oracle_address)

        # Faucet parameters
        self.storage.set("base_gold_reward", U128(100)) # 100 gold
        self.storage.set("min_claim_interval", U64(3600)) # 1 hour
        self.storage.set("halving_threshold", U128(1000000)) # Halve rewards every 1M tokens minted
        self.storage.set("total_gold_fauceted", U128(0))

        # Sink parameters
        self.storage.set("sink_multiplier_bps", U64(10000)) # 100.00% (base multiplier)
        self.storage.set("stabilization_mode", False)

        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "gold_token": gold_token,
            "gem_token": gem_token
        })

    # ------------------------------------------------------------------ #
    #  Admin & Oracle Controls                                            #
    # ------------------------------------------------------------------ #

    @external
    def set_stabilization_mode(self, admin: Address, enabled: Bool):
        """Toggle economic stabilization mode manually. Only Admin."""
        self._require_admin(admin)
        self.storage.set("stabilization_mode", enabled)

        # If stabilization is active, automatically increase sinks by 50% and half faucet rewards
        if enabled:
            self.storage.set("sink_multiplier_bps", U64(15000)) # 150%
        else:
            self.storage.set("sink_multiplier_bps", U64(10000)) # 100%

        self.env.emit_event("stabilization_toggled", {"enabled": enabled})

    @external
    def update_price_ratio(self, oracle: Address, gold_per_gem: U128):
        """Update system state from the oracle price feed. Triggers stabilization automatically if ratio drops."""
        self._require_initialized()
        oracle.require_auth()

        expected_oracle = self.storage.get("oracle_address")
        if oracle != expected_oracle:
            raise ContractError.UNAUTHORIZED

        # Normal threshold is 100 Gold per Gem
        # If it drops below 50, trigger stabilization policy to protect economy
        prev_mode = self.storage.get("stabilization_mode")
        
        if gold_per_gem < U128(50):
            self.storage.set("stabilization_mode", True)
            self.storage.set("sink_multiplier_bps", U64(20000)) # 200% sink cost
            self.env.emit_event("stabilization_triggered_by_oracle", {"ratio": gold_per_gem})
        else:
            # If it recovery, reset to admin settings
            if prev_mode:
                self.storage.set("stabilization_mode", False)
                self.storage.set("sink_multiplier_bps", U64(10000)) # Reset to 100%
                self.env.emit_event("stabilization_lifted_by_oracle", {"ratio": gold_per_gem})

    @external
    def configure_sink(self, admin: Address, sink_id: Symbol, base_cost: U128):
        """Configure a game token sink (e.g. "crafting_upgrade", "skin_purchase"). Only Admin."""
        self._require_admin(admin)
        self.storage.set(("sink_base_cost", sink_id), base_cost)
        self.env.emit_event("sink_configured", {"sink_id": sink_id, "base_cost": base_cost})

    # ------------------------------------------------------------------ #
    #  Player Actions (Faucet & Sinks)                                   #
    # ------------------------------------------------------------------ #

    @external
    def claim_faucet(self, player: Address) -> U128:
        """Claim gold tokens from the play-to-earn faucet. Includes bot-detection logic."""
        self._require_initialized()
        player.require_auth()

        now = self.env.ledger().timestamp()

        # Check rate limits
        last_claim = self.storage.get(("last_claim", player), U64(0))
        min_interval = self.storage.get("min_claim_interval")
        if now - last_claim < min_interval:
            raise ContractError.CLAIM_RATE_LIMIT

        # Bot pattern analysis
        # Collect interval history
        intervals = self.storage.get(("claim_intervals", player), None)
        if intervals is None:
            intervals = Vec()
        
        current_interval = now - last_claim

        # Check if user claims at exactly the minimum rate limit repeatedly (signature of a bot script)
        # We calculate variance of last few intervals
        if len(intervals) >= 3:
            # Shift left and add new
            intervals.set(0, intervals[1])
            intervals.set(1, intervals[2])
            intervals.set(2, current_interval)
        else:
            intervals.push_back(current_interval)

        self.storage.set(("claim_intervals", player), intervals)

        if len(intervals) == 3:
            diff1 = self._abs_diff(intervals[0], intervals[1])
            diff2 = self._abs_diff(intervals[1], intervals[2])
            # If the difference between intervals is less than 3 seconds, flag as bot
            if diff1 < U64(3) and diff2 < U64(3):
                self.env.emit_event("bot_flagged", {"player": player, "intervals": intervals})
                raise ContractError.BOT_PATTERN_DETECTED

        # Calculate reward based on inflation schedule
        reward = self._calculate_current_reward()

        # Apply stabilization penalty (cut faucet rewards by 50% if stabilization is active)
        if self.storage.get("stabilization_mode"):
            reward = reward / U128(2)

        # Update player claim logs
        self.storage.set(("last_claim", player), now)
        
        total_fauceted = self.storage.get("total_gold_fauceted") + reward
        self.storage.set("total_gold_fauceted", total_fauceted)

        # Mint gold tokens to player
        gold_token = self.storage.get("gold_token")
        contract_addr = self.env.current_contract_address()
        success = self.env.invoke_contract(gold_token, "mint", [contract_addr, player, reward])
        if not success:
            raise ContractError.TRANSFER_FAILED

        self.env.emit_event("faucet_claimed", {
            "player": player,
            "reward": reward,
            "timestamp": now
        })

        return reward

    @external
    def execute_sink(self, player: Address, sink_id: Symbol) -> U128:
        """Charge player gold for game activities, burning/locking the tokens."""
        self._require_initialized()
        player.require_auth()

        base_cost = self.storage.get(("sink_base_cost", sink_id), None)
        if base_cost is None:
            raise ContractError.SINK_NOT_FOUND

        # Apply dynamic multiplier based on stabilization state
        multiplier = self.storage.get("sink_multiplier_bps")
        actual_cost = (base_cost * U128(multiplier)) / U128(10000)

        # Transfer tokens to this contract (or burn them by sending to dead address)
        gold_token = self.storage.get("gold_token")
        contract_addr = self.env.current_contract_address()
        
        # Charge player
        success = self.env.invoke_contract(gold_token, "transfer", [player, contract_addr, actual_cost])
        if not success:
            raise ContractError.TRANSFER_FAILED

        # Burn standard: transfer to null address or burn method
        # Here we invoke transfer to a zero placeholder to simulate burn
        burn_address = Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF")
        self.env.invoke_contract(gold_token, "transfer", [contract_addr, burn_address, actual_cost])

        self.env.emit_event("sink_executed", {
            "player": player,
            "sink_id": sink_id,
            "amount_burned": actual_cost
        })

        return actual_cost

    # ------------------------------------------------------------------ #
    #  View Operations                                                    #
    # ------------------------------------------------------------------ #

    @view
    def get_faucet_info(self) -> Map:
        """Get information about faucet stats."""
        self._require_initialized()
        res = Map()
        res.set(Symbol("total_fauceted"), self.storage.get("total_gold_fauceted"))
        res.set(Symbol("current_reward"), self._calculate_current_reward())
        res.set(Symbol("stabilization_active"), self.storage.get("stabilization_mode"))
        res.set(Symbol("sink_multiplier"), self.storage.get("sink_multiplier_bps"))
        return res

    @view
    def get_player_claim_data(self, player: Address) -> Map:
        """Get claim timestamp and intervals of a player."""
        self._require_initialized()
        res = Map()
        res.set(Symbol("last_claim"), self.storage.get(("last_claim", player), U64(0)))
        res.set(Symbol("intervals"), self.storage.get(("claim_intervals", player), Vec()))
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

    def _abs_diff(self, a: U64, b: U64) -> U64:
        if a > b:
            return a - b
        return b - a

    def _calculate_current_reward(self) -> U128:
        """Calculate reward with halving schedule based on total minted tokens."""
        total_fauceted = self.storage.get("total_gold_fauceted")
        halving_threshold = self.storage.get("halving_threshold")
        base_reward = self.storage.get("base_gold_reward")

        # Determine number of halvings
        halvings = total_fauceted / halving_threshold
        
        # Max out halvings to 10 to avoid division by zero
        if halvings > U128(10):
            halvings = U128(10)

        # Reward = base_reward / (2 ^ halvings)
        divisor = U128(1) << int(halvings)
        return base_reward / divisor
