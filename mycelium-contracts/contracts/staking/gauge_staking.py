"""
GaugeStaking — Gauge weight voting and emission controller.

Mycelium Smart Contract for Stellar
Allows veToken holders to vote on gauge weights to redirect reward emissions.
Features gauge checkpointing with decaying weights and gauge type weights.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


# ── Error Codes ──────────────────────────────────────────────────────────────

class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    GAUGE_NOT_FOUND = 4
    GAUGE_ALREADY_EXISTS = 5
    INVALID_WEIGHT_BPS = 6
    TOTAL_WEIGHT_EXCEEDED = 7
    INVALID_TYPE = 8
    NO_ACTIVE_LOCK = 9


# ── Constants ────────────────────────────────────────────────────────────────

WEEK = U64(7 * 86400)                     # 1 week in seconds
MAX_TIME = U64(4 * 365 * 86400)           # 4 years in seconds
BPS_BASE = U64(10000)                     # 100% in basis points
PRECISION = U128(1_000_000_000_000)       # 1e12 for scaling calculations


@contract
class GaugeStaking:
    """
    Gauge Staking contract. Users allocate decaying veToken voting power to
    different reward gauges, which defines the relative emission weights.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ── Admin & Initialization ───────────────────────────────────────────

    @external
    def initialize(self, admin: Address, ve_token: Address):
        """
        One-time initialization. Sets admin and veToken contract.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("ve_token", ve_token)
        self.storage.set("gauges", Vec())
        self.storage.set("initialized", True)

        # Global checkpoints setup
        now = self.env.ledger().timestamp()
        rounded_now = (now / WEEK) * WEEK
        self.storage.set("global_checkpoint_count", U64(1))
        
        initial_checkpoint = {
            "timestamp": rounded_now,
            "bias": U128(0),
            "slope": U128(0),
        }
        self.storage.set("global_checkpoint:0", initial_checkpoint)
        self.storage.set("last_global_checkpoint_time", rounded_now)

        self.env.emit_event("initialized", {
            "admin": admin,
            "ve_token": ve_token,
            "timestamp": rounded_now,
        })

    @external
    def add_gauge(self, caller: Address, gauge: Address, gauge_type: U64):
        """
        Admin-only: register a new reward gauge and assign it to a gauge type.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        if self._is_gauge(gauge):
            raise ContractError.GAUGE_ALREADY_EXISTS

        gauges = self.storage.get("gauges")
        gauges.append(gauge)
        self.storage.set("gauges", gauges)

        self.storage.set(f"gauge_type:{gauge}", gauge_type)
        
        # Setup gauge checkpoints
        now = self.env.ledger().timestamp()
        rounded_now = (now / WEEK) * WEEK
        self.storage.set(f"gauge_checkpoint_count:{gauge}", U64(1))
        
        initial_checkpoint = {
            "timestamp": rounded_now,
            "bias": U128(0),
            "slope": U128(0),
        }
        self.storage.set(f"gauge_checkpoint:{gauge}:0", initial_checkpoint)
        self.storage.set(f"gauge_last_checkpoint_time:{gauge}", rounded_now)

        self.env.emit_event("gauge_added", {
            "gauge": gauge,
            "gauge_type": gauge_type,
            "timestamp": rounded_now,
        })

    @external
    def set_type_weight(self, caller: Address, type_id: U64, weight_bps: U64):
        """
        Admin-only: configure weight multiplier for a gauge type (e.g. 20000 = 2.0x boost).
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        self.storage.set(f"type_weight:{type_id}", weight_bps)

        self.env.emit_event("type_weight_updated", {
            "type_id": type_id,
            "weight_bps": weight_bps,
        })

    # ── User Voting Actions ──────────────────────────────────────────────

    @external
    def vote_for_gauge_weight(self, user: Address, gauge: Address, weight_bps: U64):
        """
        Allocate percentage of veToken voting power to a specific gauge.
        Cumulative weight across all gauges for a user must be <= 10000 bps (100%).
        """
        user.require_auth()
        self._require_initialized()

        if not self._is_gauge(gauge):
            raise ContractError.GAUGE_NOT_FOUND

        if weight_bps > BPS_BASE:
            raise ContractError.INVALID_WEIGHT_BPS

        # Check total allocated weight limits
        old_weight = self.storage.get(f"user_vote:{user}:{gauge}", U64(0))
        total_allocated = self.storage.get(f"user_total_vote:{user}", U64(0))
        
        new_total_allocated = (total_allocated - old_weight) + weight_bps
        if new_total_allocated > BPS_BASE:
            raise ContractError.TOTAL_WEIGHT_EXCEEDED

        self.storage.set(f"user_vote:{user}:{gauge}", weight_bps)
        self.storage.set(f"user_total_vote:{user}", new_total_allocated)

        # Retrieve user lock parameters from veToken contract
        ve_token = self.storage.get("ve_token")
        
        now = self.env.ledger().timestamp()
        
        # Get lock details (amount, end)
        lock = self.env.call(ve_token, "get_lock", [user])
        if lock["amount"] == U128(0) or lock["end"] <= now:
            raise ContractError.NO_ACTIVE_LOCK

        # Compute user's slope and bias contributions
        user_slope = (lock["amount"] * PRECISION) / U128(MAX_TIME)
        user_dt = U128(lock["end"] - now)
        user_bias = (user_slope * user_dt) / PRECISION

        # Scale contributions by vote weight allocation
        old_contrib_slope = (user_slope * U128(old_weight)) / U128(BPS_BASE)
        old_contrib_bias = (user_bias * U128(old_weight)) / U128(BPS_BASE)

        new_contrib_slope = (user_slope * U128(weight_bps)) / U128(BPS_BASE)
        new_contrib_bias = (user_bias * U128(weight_bps)) / U128(BPS_BASE)

        old_lock_data = {
            "amount": lock["amount"],
            "end": lock["end"],
            "slope": old_contrib_slope,
            "bias": old_contrib_bias,
        }
        new_lock_data = {
            "amount": lock["amount"],
            "end": lock["end"],
            "slope": new_contrib_slope,
            "bias": new_contrib_bias,
        }

        # Checkpoint gauge and global state
        self._checkpoint_gauge(gauge, now, old_lock_data, new_lock_data)
        self._checkpoint_global(now, old_lock_data, new_lock_data)

        self.env.emit_event("voted", {
            "user": user,
            "gauge": gauge,
            "weight_bps": weight_bps,
        })

    # ── View Functions ───────────────────────────────────────────────────

    @view
    def get_gauge_weight(self, gauge: Address, timestamp: U64) -> U128:
        """
        Get the checkpointed voting weight of a gauge at a timestamp.
        """
        count = self.storage.get(f"gauge_checkpoint_count:{gauge}", U64(0))
        if count == U64(0):
            return U128(0)

        idx = self._find_gauge_checkpoint(gauge, timestamp, count)
        checkpoint = self.storage.get(f"gauge_checkpoint:{gauge}:{idx}")

        if timestamp < checkpoint["timestamp"]:
            return U128(0)

        dt = timestamp - checkpoint["timestamp"]
        slope = checkpoint["slope"]
        bias = checkpoint["bias"]
        decay = (slope * U128(dt)) / PRECISION

        if bias <= decay:
            return U128(0)
        return bias - decay

    @view
    def get_total_weight(self, timestamp: U64) -> U128:
        """
        Get the checkpointed global voting weight across all gauges at a timestamp.
        """
        count = self.storage.get("global_checkpoint_count", U64(0))
        if count == U64(0):
            return U128(0)

        idx = self._find_global_checkpoint(timestamp, count)
        checkpoint = self.storage.get(f"global_checkpoint:{idx}")

        if timestamp < checkpoint["timestamp"]:
            return U128(0)

        dt = timestamp - checkpoint["timestamp"]
        slope = checkpoint["slope"]
        bias = checkpoint["bias"]
        decay = (slope * U128(dt)) / PRECISION

        if bias <= decay:
            return U128(0)
        return bias - decay

    @view
    def get_relative_weight(self, gauge: Address, timestamp: U64) -> U128:
        """
        Get the relative weight share of a gauge (multiplied by gauge type weights)
        relative to the global total weight at a timestamp.
        Returns weight scaled by 1e12 (PRECISION).
        """
        gauge_weight = self.get_gauge_weight(gauge, timestamp)
        if gauge_weight == U128(0):
            return U128(0)

        total_weight = self.get_total_weight(timestamp)
        if total_weight == U128(0):
            return U128(0)

        gauge_type = self.storage.get(f"gauge_type:{gauge}", U64(0))
        type_weight = self.storage.get(f"type_weight:{gauge_type}", BPS_BASE)

        weighted_gauge = (gauge_weight * U128(type_weight)) / U128(BPS_BASE)
        return (weighted_gauge * PRECISION) / total_weight

    @view
    def get_gauges(self) -> Vec:
        """
        Get list of registered gauges.
        """
        return self.storage.get("gauges")

    # ── Checkpoint & Binary Search Helpers ───────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _is_gauge(self, gauge: Address) -> Bool:
        gauges = self.storage.get("gauges")
        for i in range(gauges.len()):
            if gauges.get(i) == gauge:
                return True
        return False

    def _find_gauge_checkpoint(self, gauge: Address, timestamp: U64, count: U64) -> U64:
        low = U64(0)
        high = count - U64(1)

        while low < high:
            mid = (low + high + U64(1)) / U64(2)
            mid_checkpoint = self.storage.get(f"gauge_checkpoint:{gauge}:{mid}")
            if mid_checkpoint["timestamp"] <= timestamp:
                low = mid
            else:
                high = mid - U64(1)
        return low

    def _find_global_checkpoint(self, timestamp: U64, count: U64) -> U64:
        low = U64(0)
        high = count - U64(1)

        while low < high:
            mid = (low + high + U64(1)) / U64(2)
            mid_checkpoint = self.storage.get(f"global_checkpoint:{mid}")
            if mid_checkpoint["timestamp"] <= timestamp:
                low = mid
            else:
                high = mid - U64(1)
        return low

    def _checkpoint_gauge(
        self,
        gauge: Address,
        now: U64,
        old_data: Map,
        new_data: Map,
    ):
        """
        Step forward week-by-week and update checkpoints for a specific gauge.
        """
        last_checkpoint_time = self.storage.get(f"gauge_last_checkpoint_time:{gauge}")
        gauge_count = self.storage.get(f"gauge_checkpoint_count:{gauge}")
        last_checkpoint = self.storage.get(f"gauge_checkpoint:{gauge}:{gauge_count - U64(1)}")

        last_bias = last_checkpoint["bias"]
        last_slope = last_checkpoint["slope"]

        # 1. Update slope changes for this gauge
        if old_data["slope"] > U128(0):
            old_end = old_data["end"]
            current_change = self.storage.get(f"gauge_slope_change:{gauge}:{old_end}", U128(0))
            if current_change >= old_data["slope"]:
                self.storage.set(f"gauge_slope_change:{gauge}:{old_end}", current_change - old_data["slope"])
            else:
                self.storage.set(f"gauge_slope_change:{gauge}:{old_end}", U128(0))

        if new_data["slope"] > U128(0) and new_data["end"] > now:
            new_end = new_data["end"]
            current_change = self.storage.get(f"gauge_slope_change:{gauge}:{new_end}", U128(0))
            self.storage.set(f"gauge_slope_change:{gauge}:{new_end}", current_change + new_data["slope"])

        # 2. Iterate weekly checkpoints to now
        t = (last_checkpoint_time / WEEK) * WEEK
        while t < now:
            t += WEEK
            dt = WEEK
            if t > now:
                dt = now - (t - WEEK)
                t = now

            decay = (last_slope * U128(dt)) / PRECISION
            last_bias = last_bias - decay if last_bias > decay else U128(0)

            if t % WEEK == U64(0):
                change = self.storage.get(f"gauge_slope_change:{gauge}:{t}", U128(0))
                last_slope = last_slope - change if last_slope > change else U128(0)

            new_checkpoint = {
                "timestamp": t,
                "bias": last_bias,
                "slope": last_slope,
            }
            self.storage.set(f"gauge_checkpoint:{gauge}:{gauge_count}", new_checkpoint)
            gauge_count += U64(1)

        # 3. Apply state change delta for this vote
        # Subtract old user contribution
        if old_data["slope"] > U128(0):
            last_slope = last_slope - old_data["slope"] if last_slope > old_data["slope"] else U128(0)
            last_bias = last_bias - old_data["bias"] if last_bias > old_data["bias"] else U128(0)

        # Add new user contribution
        if new_data["slope"] > U128(0) and new_data["end"] > now:
            last_slope += new_data["slope"]
            last_bias += new_data["bias"]

        # Save final checkpoint
        final_checkpoint = {
            "timestamp": now,
            "bias": last_bias,
            "slope": last_slope,
        }
        self.storage.set(f"gauge_checkpoint:{gauge}:{gauge_count - U64(1)}", final_checkpoint)
        self.storage.set(f"gauge_last_checkpoint_time:{gauge}", now)
        self.storage.set(f"gauge_checkpoint_count:{gauge}", gauge_count)

    def _checkpoint_global(self, now: U64, old_data: Map, new_data: Map):
        """
        Step forward week-by-week and update checkpoints for global total weight.
        """
        last_checkpoint_time = self.storage.get("last_global_checkpoint_time")
        global_count = self.storage.get("global_checkpoint_count")
        last_checkpoint = self.storage.get(f"global_checkpoint:{global_count - U64(1)}")

        last_bias = last_checkpoint["bias"]
        last_slope = last_checkpoint["slope"]

        # 1. Update slope changes globally
        if old_data["slope"] > U128(0):
            old_end = old_data["end"]
            current_change = self.storage.get(f"global_slope_change:{old_end}", U128(0))
            if current_change >= old_data["slope"]:
                self.storage.set(f"global_slope_change:{old_end}", current_change - old_data["slope"])
            else:
                self.storage.set(f"global_slope_change:{old_end}", U128(0))

        if new_data["slope"] > U128(0) and new_data["end"] > now:
            new_end = new_data["end"]
            current_change = self.storage.get(f"global_slope_change:{new_end}", U128(0))
            self.storage.set(f"global_slope_change:{new_end}", current_change + new_data["slope"])

        # 2. Iterate weekly checkpoints to now
        t = (last_checkpoint_time / WEEK) * WEEK
        while t < now:
            t += WEEK
            dt = WEEK
            if t > now:
                dt = now - (t - WEEK)
                t = now

            decay = (last_slope * U128(dt)) / PRECISION
            last_bias = last_bias - decay if last_bias > decay else U128(0)

            if t % WEEK == U64(0):
                change = self.storage.get(f"global_slope_change:{t}", U128(0))
                last_slope = last_slope - change if last_slope > change else U128(0)

            new_checkpoint = {
                "timestamp": t,
                "bias": last_bias,
                "slope": last_slope,
            }
            self.storage.set(f"global_checkpoint:{global_count}", new_checkpoint)
            global_count += U64(1)

        # 3. Apply state change delta for this vote
        # Subtract old user contribution
        if old_data["slope"] > U128(0):
            last_slope = last_slope - old_data["slope"] if last_slope > old_data["slope"] else U128(0)
            last_bias = last_bias - old_data["bias"] if last_bias > old_data["bias"] else U128(0)

        # Add new user contribution
        if new_data["slope"] > U128(0) and new_data["end"] > now:
            last_slope += new_data["slope"]
            last_bias += new_data["bias"]

        # Save final checkpoint
        final_checkpoint = {
            "timestamp": now,
            "bias": last_bias,
            "slope": last_slope,
        }
        self.storage.set(f"global_checkpoint:{global_count - U64(1)}", final_checkpoint)
        self.storage.set("last_global_checkpoint_time", now)
        self.storage.set("global_checkpoint_count", global_count)
