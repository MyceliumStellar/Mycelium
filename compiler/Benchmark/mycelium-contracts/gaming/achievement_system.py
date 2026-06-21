"""
Achievement System — Soulbound badge unlocks, cumulative criteria progress, hidden achievements, and gamerscore indexes.

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
    ACHIEVEMENT_NOT_FOUND = 4
    ALREADY_UNLOCKED = 5
    INVALID_THRESHOLD = 6
    INVALID_CRITERIA = 7


@contract
class AchievementSystem:
    """Manages player achievements, cumulative criteria counts, hidden badges, and player gamestats."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """Initialize the Achievement System contract."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("achievement_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin})

    # ------------------------------------------------------------------ #
    #  Admin Operations                                                   #
    # ------------------------------------------------------------------ #

    @external
    def register_achievement(
        self,
        admin: Address,
        achievement_id: U64,
        name: Symbol,
        criteria_type: Symbol,
        threshold: U64,
        gamerscore: U64,
        is_hidden: Bool
    ):
        """Register a new gaming achievement badge. Only Admin."""
        self._require_admin(admin)
        if threshold == U64(0):
            raise ContractError.INVALID_THRESHOLD
        if criteria_type == Symbol(""):
            raise ContractError.INVALID_CRITERIA

        # Check if already exists
        existing = self.storage.get(("achievement", achievement_id), None)

        achievement = {
            "id": achievement_id,
            "name": name,
            "criteria_type": criteria_type,
            "threshold": threshold,
            "gamerscore": gamerscore,
            "is_hidden": is_hidden
        }

        self.storage.set(("achievement", achievement_id), achievement)

        if existing is None:
            count = self.storage.get("achievement_count") + U64(1)
            self.storage.set("achievement_count", count)

        self.env.emit_event("achievement_registered", {
            "id": achievement_id,
            "name": name,
            "criteria": criteria_type,
            "threshold": threshold,
            "is_hidden": is_hidden
        })

    @external
    def set_reporter(self, admin: Address, reporter: Address, status: Bool):
        """Set authorization status of a reporter game contract. Only Admin."""
        self._require_admin(admin)
        self.storage.set(("reporter", reporter), status)
        self.env.emit_event("reporter_status_updated", {"reporter": reporter, "status": status})

    # ------------------------------------------------------------------ #
    #  Player Progress Tracking                                           #
    # ------------------------------------------------------------------ #

    @external
    def update_progress(
        self,
        reporter: Address,
        player: Address,
        criteria_type: Symbol,
        increment: U64
    ) -> U64:
        """Update a player's cumulative progress metrics. Triggers badge unlocks if thresholds met."""
        self._require_initialized()
        reporter.require_auth()
        self._require_reporter(reporter)

        # Get existing progress
        curr_progress = self.storage.get(("progress", player, criteria_type), U64(0))
        new_progress = curr_progress + increment
        self.storage.set(("progress", player, criteria_type), new_progress)

        self.env.emit_event("progress_updated", {
            "player": player,
            "criteria": criteria_type,
            "progress": new_progress
        })

        # Scan and evaluate unlock conditions for achievements matching this criteria
        # In a real-world setting, we'd limit this or query a list. Since achievement counts are bounded,
        # we iterate or run lookup indexes.
        # We will loop through registered achievements from ID 1 up to achievement_count
        count = self.storage.get("achievement_count")
        for i in range(1, int(count) + 1):
            ach_id = U64(i)
            ach = self.storage.get(("achievement", ach_id), None)
            if ach is not None and ach["criteria_type"] == criteria_type:
                # Check if not already unlocked
                if not self.storage.get(("unlocked", player, ach_id), False):
                    if new_progress >= ach["threshold"]:
                        self._unlock_achievement_badge(player, ach)

        return new_progress

    @external
    def unlock_achievement_direct(self, reporter: Address, player: Address, achievement_id: U64):
        """Force unlock a specific achievement badge directly (e.g. for event achievements)."""
        self._require_initialized()
        reporter.require_auth()
        self._require_reporter(reporter)

        ach = self.storage.get(("achievement", achievement_id), None)
        if ach is None:
            raise ContractError.ACHIEVEMENT_NOT_FOUND

        if self.storage.get(("unlocked", player, achievement_id), False):
            raise ContractError.ALREADY_UNLOCKED

        self._unlock_achievement_badge(player, ach)

    # ------------------------------------------------------------------ #
    #  View Operations                                                    #
    # ------------------------------------------------------------------ #

    @view
    def get_player_gamerscore(self, player: Address) -> U64:
        """Get total gamerscore points of a player."""
        self._require_initialized()
        return self.storage.get(("gamerscore", player), U64(0))

    @view
    def get_progress(self, player: Address, criteria_type: Symbol) -> U64:
        """Get progress count of a player for a criteria."""
        self._require_initialized()
        return self.storage.get(("progress", player, criteria_type), U64(0))

    @view
    def has_badge(self, player: Address, achievement_id: U64) -> Bool:
        """Check if a player has unlocked a specific badge."""
        self._require_initialized()
        return self.storage.get(("unlocked", player, achievement_id), False)

    @view
    def get_achievement(self, player: Address, achievement_id: U64) -> Map:
        """Get achievement details. If hidden and locked, returns masked info."""
        self._require_initialized()
        ach = self.storage.get(("achievement", achievement_id), None)
        if ach is None:
            raise ContractError.ACHIEVEMENT_NOT_FOUND

        is_unlocked = self.storage.get(("unlocked", player, achievement_id), False)

        res = Map()
        res.set(Symbol("id"), ach["id"])
        
        if ach["is_hidden"] and not is_unlocked:
            res.set(Symbol("name"), Symbol("Hidden Achievement"))
            res.set(Symbol("criteria_type"), Symbol("unknown"))
            res.set(Symbol("threshold"), U64(0))
            res.set(Symbol("gamerscore"), ach["gamerscore"])
            res.set(Symbol("unlocked"), False)
        else:
            res.set(Symbol("name"), ach["name"])
            res.set(Symbol("criteria_type"), ach["criteria_type"])
            res.set(Symbol("threshold"), ach["threshold"])
            res.set(Symbol("gamerscore"), ach["gamerscore"])
            res.set(Symbol("unlocked"), is_unlocked)

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

    def _require_reporter(self, caller: Address):
        admin = self.storage.get("admin")
        if caller == admin:
            return
        if not self.storage.get(("reporter", caller), False):
            raise ContractError.UNAUTHORIZED

    def _unlock_achievement_badge(self, player: Address, ach: Map):
        """Mark achievement unlocked and award gamerscore."""
        ach_id = ach["id"]
        self.storage.set(("unlocked", player, ach_id), True)

        curr_gs = self.storage.get(("gamerscore", player), U64(0))
        self.storage.set(("gamerscore", player), curr_gs + ach["gamerscore"])

        self.env.emit_event("achievement_unlocked", {
            "player": player,
            "achievement_id": ach_id,
            "gamerscore_awarded": ach["gamerscore"]
        })
