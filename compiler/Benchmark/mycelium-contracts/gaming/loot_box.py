"""
Loot Box System — Randomized rewards with pity counters, seasonal rotations, and rarity drop pools.

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
    INSUFFICIENT_BALANCE = 4
    TRANSFER_FAILED = 5
    SEASON_NOT_ACTIVE = 6
    EMPTY_DROP_POOL = 7
    INVALID_WEIGHTS = 8
    INVALID_PITY_THRESHOLD = 9
    INVALID_SEASON = 10


class Rarity:
    COMMON = 0
    UNCOMMON = 1
    RARE = 2
    EPIC = 3
    LEGENDARY = 4


@contract
class LootBoxSystem:
    """Manages randomized loot box openings, seasonal item pools, and pity mechanisms."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        gold_token: Address,
        default_cost: U128,
        pity_threshold: U64
    ):
        """Initialize the Loot Box contract with admin and cost parameters."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        if pity_threshold == U64(0):
            raise ContractError.INVALID_PITY_THRESHOLD

        self.storage.set("admin", admin)
        self.storage.set("gold_token", gold_token)
        self.storage.set("loot_box_cost", default_cost)
        self.storage.set("pity_threshold", pity_threshold)
        self.storage.set("active_season", U64(1))
        self.storage.set("global_opens", U64(0))
        self.storage.set("initialized", True)

        # Set default rarity weights for season 1 (basis points: total 10000)
        # Common: 7000 (70%), Uncommon: 2000 (20%), Rare: 800 (8%), Epic: 180 (1.8%), Legendary: 20 (0.2%)
        self.storage.set(("weights", U64(1), U64(Rarity.COMMON)), U64(7000))
        self.storage.set(("weights", U64(1), U64(Rarity.UNCOMMON)), U64(2000))
        self.storage.set(("weights", U64(1), U64(Rarity.RARE)), U64(800))
        self.storage.set(("weights", U64(1), U64(Rarity.EPIC)), U64(180))
        self.storage.set(("weights", U64(1), U64(Rarity.LEGENDARY)), U64(20))

        self.env.emit_event("initialized", {
            "admin": admin,
            "gold_token": gold_token,
            "default_cost": default_cost,
            "pity_threshold": pity_threshold
        })

    # ------------------------------------------------------------------ #
    #  Admin Operations                                                   #
    # ------------------------------------------------------------------ #

    @external
    def configure_season(self, admin: Address, season_id: U64, active: Bool):
        """Enable or disable a season pool. Only Admin."""
        self._require_admin(admin)
        if season_id == U64(0):
            raise ContractError.INVALID_SEASON

        self.storage.set(("season_active", season_id), active)
        self.env.emit_event("season_configured", {"season_id": season_id, "active": active})

    @external
    def set_active_season(self, admin: Address, season_id: U64):
        """Set the current active loot box season. Only Admin."""
        self._require_admin(admin)
        if season_id == U64(0):
            raise ContractError.INVALID_SEASON
        
        # Ensure the season is configured as active
        if not self.storage.get(("season_active", season_id), True):
            raise ContractError.SEASON_NOT_ACTIVE

        self.storage.set("active_season", season_id)
        self.env.emit_event("active_season_updated", {"season_id": season_id})

    @external
    def set_loot_box_cost(self, admin: Address, new_cost: U128):
        """Update the gold token cost to open a loot box. Only Admin."""
        self._require_admin(admin)
        self.storage.set("loot_box_cost", new_cost)
        self.env.emit_event("cost_updated", {"new_cost": new_cost})

    @external
    def set_pity_threshold(self, admin: Address, threshold: U64):
        """Update the legendary pity count threshold. Only Admin."""
        self._require_admin(admin)
        if threshold == U64(0):
            raise ContractError.INVALID_PITY_THRESHOLD

        self.storage.set("pity_threshold", threshold)
        self.env.emit_event("pity_threshold_updated", {"threshold": threshold})

    @external
    def set_season_weights(
        self,
        admin: Address,
        season_id: U64,
        common_w: U64,
        uncommon_w: U64,
        rare_w: U64,
        epic_w: U64,
        legendary_w: U64
    ):
        """Set rarity weights (bps) for a specific season. Total must equal 10000."""
        self._require_admin(admin)
        if common_w + uncommon_w + rare_w + epic_w + legendary_w != U64(10000):
            raise ContractError.INVALID_WEIGHTS

        self.storage.set(("weights", season_id, U64(Rarity.COMMON)), common_w)
        self.storage.set(("weights", season_id, U64(Rarity.UNCOMMON)), uncommon_w)
        self.storage.set(("weights", season_id, U64(Rarity.RARE)), rare_w)
        self.storage.set(("weights", season_id, U64(Rarity.EPIC)), epic_w)
        self.storage.set(("weights", season_id, U64(Rarity.LEGENDARY)), legendary_w)

        self.env.emit_event("season_weights_updated", {
            "season_id": season_id,
            "weights": [common_w, uncommon_w, rare_w, epic_w, legendary_w]
        })

    @external
    def update_drop_pool(
        self,
        admin: Address,
        season_id: U64,
        rarity: U64,
        items: Vec
    ):
        """Update the list of items available in a rarity tier for a season. Only Admin."""
        self._require_admin(admin)
        if rarity > U64(Rarity.LEGENDARY):
            raise ContractError.INVALID_WEIGHTS

        self.storage.set(("drop_pool", season_id, rarity), items)
        self.env.emit_event("drop_pool_updated", {
            "season_id": season_id,
            "rarity": rarity,
            "item_count": U64(len(items))
        })

    # ------------------------------------------------------------------ #
    #  Player Operations                                                  #
    # ------------------------------------------------------------------ #

    @external
    def open_loot_box(self, player: Address) -> Symbol:
        """Open a loot box using payment tokens, applying pity mechanics and random rolls."""
        self._require_initialized()
        player.require_auth()

        season_id = self.storage.get("active_season")
        if not self.storage.get(("season_active", season_id), True):
            raise ContractError.SEASON_NOT_ACTIVE

        # Charge player for loot box
        cost = self.storage.get("loot_box_cost")
        if cost > U128(0):
            gold_token = self.storage.get("gold_token")
            contract_addr = self.env.current_contract_address()
            success = self.env.invoke_contract(gold_token, "transfer", [player, contract_addr, cost])
            if not success:
                raise ContractError.TRANSFER_FAILED

        # Increment global open counter for entropy
        global_opens = self.storage.get("global_opens") + U64(1)
        self.storage.set("global_opens", global_opens)

        # Handle pity counter
        pity_count = self.storage.get(("pity", player), U64(0)) + U64(1)
        pity_threshold = self.storage.get("pity_threshold")

        rarity_won = U64(Rarity.COMMON)
        is_pity_trigger = False

        if pity_count >= pity_threshold:
            # Trigger legendary pity drop
            rarity_won = U64(Rarity.LEGENDARY)
            is_pity_trigger = True
            self.storage.set(("pity", player), U64(0))
        else:
            # Normal pseudo-random roll (0 to 9999 basis points)
            now = self.env.ledger().timestamp()
            entropy = self.env.crypto().keccak256(now, player, global_opens)
            roll = U64(int(entropy[31])) * U64(39) % U64(10000)

            # Cumulative weights search
            w_common = self.storage.get(("weights", season_id, U64(Rarity.COMMON)), U64(0))
            w_uncommon = self.storage.get(("weights", season_id, U64(Rarity.UNCOMMON)), U64(0))
            w_rare = self.storage.get(("weights", season_id, U64(Rarity.RARE)), U64(0))
            w_epic = self.storage.get(("weights", season_id, U64(Rarity.EPIC)), U64(0))

            if roll < w_common:
                rarity_won = U64(Rarity.COMMON)
            elif roll < w_common + w_uncommon:
                rarity_won = U64(Rarity.UNCOMMON)
            elif roll < w_common + w_uncommon + w_rare:
                rarity_won = U64(Rarity.RARE)
            elif roll < w_common + w_uncommon + w_rare + w_epic:
                rarity_won = U64(Rarity.EPIC)
            else:
                rarity_won = U64(Rarity.LEGENDARY)

            # Update pity track
            if rarity_won == U64(Rarity.LEGENDARY):
                self.storage.set(("pity", player), U64(0))
            else:
                self.storage.set(("pity", player), pity_count)

        # Draw specific item from rarity drop pool
        item_pool = self.storage.get(("drop_pool", season_id, rarity_won), None)
        if item_pool is None or len(item_pool) == 0:
            raise ContractError.EMPTY_DROP_POOL

        # Secondary random roll for item index inside the pool
        now_item = self.env.ledger().timestamp()
        item_entropy = self.env.crypto().keccak256(now_item, player, global_opens, rarity_won)
        item_roll = U64(int(item_entropy[30])) * U64(43) % U64(len(item_pool))

        item_won = item_pool[item_roll]

        self.env.emit_event("loot_box_opened", {
            "player": player,
            "season_id": season_id,
            "rarity": rarity_won,
            "item": item_won,
            "pity_triggered": is_pity_trigger,
            "new_pity_count": self.storage.get(("pity", player), U64(0))
        })

        return item_won

    # ------------------------------------------------------------------ #
    #  View Operations                                                    #
    # ------------------------------------------------------------------ #

    @view
    def get_pity_count(self, player: Address) -> U64:
        """Get the consecutive non-legendary count for a player."""
        self._require_initialized()
        return self.storage.get(("pity", player), U64(0))

    @view
    def get_season_weights(self, season_id: U64) -> Map:
        """Get the distribution weights configured for a season."""
        self._require_initialized()
        weights = Map()
        weights.set(Symbol("common"), self.storage.get(("weights", season_id, U64(Rarity.COMMON)), U64(0)))
        weights.set(Symbol("uncommon"), self.storage.get(("weights", season_id, U64(Rarity.UNCOMMON)), U64(0)))
        weights.set(Symbol("rare"), self.storage.get(("weights", season_id, U64(Rarity.RARE)), U64(0)))
        weights.set(Symbol("epic"), self.storage.get(("weights", season_id, U64(Rarity.EPIC)), U64(0)))
        weights.set(Symbol("legendary"), self.storage.get(("weights", season_id, U64(Rarity.LEGENDARY)), U64(0)))
        return weights

    @view
    def get_drop_pool(self, season_id: U64, rarity: U64) -> Vec:
        """Get list of item identifiers in a given pool."""
        self._require_initialized()
        pool = self.storage.get(("drop_pool", season_id, rarity), None)
        if pool is None:
            return Vec()
        return pool

    @view
    def get_config(self) -> Map:
        """Return global configuration variables."""
        self._require_initialized()
        config = Map()
        config.set(Symbol("admin"), self.storage.get("admin"))
        config.set(Symbol("gold_token"), self.storage.get("gold_token"))
        config.set(Symbol("cost"), self.storage.get("loot_box_cost"))
        config.set(Symbol("pity_threshold"), self.storage.get("pity_threshold"))
        config.set(Symbol("active_season"), self.storage.get("active_season"))
        return config

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
