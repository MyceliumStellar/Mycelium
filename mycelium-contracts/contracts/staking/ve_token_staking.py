"""
VeTokenStaking — Vote-Escrowed Lock (veCRV-style) Staking Contract.

Mycelium Smart Contract for Stellar
Allows locking a staking token for 1 week to 4 years to receive voting power.
Voting power decays linearly to 0 at unlock time.
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
    ZERO_AMOUNT = 4
    LOCK_NOT_EXPIRED = 5
    LOCK_EXPIRED = 6
    NO_ACTIVE_LOCK = 7
    LOCK_ALREADY_EXISTS = 8
    DURATION_OUT_OF_RANGE = 9
    UNLOCK_TIME_MUST_INCREASE = 10
    LOCK_NOT_FOUND = 11


# ── Constants ────────────────────────────────────────────────────────────────

WEEK = U64(7 * 86400)                     # 1 week in seconds
MAX_TIME = U64(4 * 365 * 86400)           # 4 years in seconds (126,144,000s)
PRECISION = U128(1_000_000_000_000)       # 1e12 for scaling calculations


@contract
class VeTokenStaking:
    """
    Vote-Escrowed lock contract. Users stake tokens to receive decaying voting power.
    Checkpoints track both user and global voting power over time.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ── Admin / Lifecycle ────────────────────────────────────────────────

    @external
    def initialize(self, admin: Address, token: Address):
        """
        One-time initialization. Sets admin and locking token.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("token", token)
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
            "token": token,
            "timestamp": rounded_now,
        })

    # ── Lock Management ──────────────────────────────────────────────────

    @external
    def create_lock(self, user: Address, amount: U128, lock_duration: U64):
        """
        Lock tokens for a duration between 1 week and 4 years.
        Rounded down to nearest week boundary.
        """
        user.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        if lock_duration < WEEK or lock_duration > MAX_TIME:
            raise ContractError.DURATION_OUT_OF_RANGE

        if self.storage.get(f"lock_exists:{user}", False):
            raise ContractError.LOCK_ALREADY_EXISTS

        now = self.env.ledger().timestamp()
        unlock_time = ((now + lock_duration) / WEEK) * WEEK

        if unlock_time <= now:
            raise ContractError.DURATION_OUT_OF_RANGE

        # Transfer tokens to contract
        token = self.storage.get("token")
        self.env.transfer(user, self.env.current_contract(), token, amount)

        lock = {
            "amount": amount,
            "end": unlock_time,
        }
        self.storage.set(f"user_lock:{user}", lock)
        self.storage.set(f"lock_exists:{user}", True)

        self._checkpoint(user, None, lock)

        self.env.emit_event("lock_created", {
            "user": user,
            "amount": amount,
            "unlock_time": unlock_time,
        })

    @external
    def increase_amount(self, user: Address, amount: U128):
        """
        Add tokens to an existing lock without changing the unlock time.
        """
        user.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        if not self.storage.get(f"lock_exists:{user}", False):
            raise ContractError.NO_ACTIVE_LOCK

        lock = self.storage.get(f"user_lock:{user}")
        now = self.env.ledger().timestamp()

        if now >= lock["end"]:
            raise ContractError.LOCK_EXPIRED

        # Transfer tokens
        token = self.storage.get("token")
        self.env.transfer(user, self.env.current_contract(), token, amount)

        old_lock = {
            "amount": lock["amount"],
            "end": lock["end"],
        }
        lock["amount"] += amount
        self.storage.set(f"user_lock:{user}", lock)

        self._checkpoint(user, old_lock, lock)

        self.env.emit_event("lock_amount_increased", {
            "user": user,
            "additional_amount": amount,
            "total_amount": lock["amount"],
        })

    @external
    def increase_unlock_time(self, user: Address, new_duration: U64):
        """
        Extend the lock time of an existing lock.
        Must be longer than current unlock time and <= 4 years.
        """
        user.require_auth()
        self._require_initialized()

        if not self.storage.get(f"lock_exists:{user}", False):
            raise ContractError.NO_ACTIVE_LOCK

        lock = self.storage.get(f"user_lock:{user}")
        now = self.env.ledger().timestamp()

        if now >= lock["end"]:
            raise ContractError.LOCK_EXPIRED

        new_unlock_time = ((now + new_duration) / WEEK) * WEEK
        if new_unlock_time <= lock["end"]:
            raise ContractError.UNLOCK_TIME_MUST_INCREASE

        if new_unlock_time > now + MAX_TIME:
            raise ContractError.DURATION_OUT_OF_RANGE

        old_lock = {
            "amount": lock["amount"],
            "end": lock["end"],
        }
        lock["end"] = new_unlock_time
        self.storage.set(f"user_lock:{user}", lock)

        self._checkpoint(user, old_lock, lock)

        self.env.emit_event("lock_time_extended", {
            "user": user,
            "new_unlock_time": new_unlock_time,
        })

    @external
    def withdraw(self, user: Address):
        """
        Withdraw locked tokens after lock duration has expired.
        Clears the lock state.
        """
        user.require_auth()
        self._require_initialized()

        if not self.storage.get(f"lock_exists:{user}", False):
            raise ContractError.NO_ACTIVE_LOCK

        lock = self.storage.get(f"user_lock:{user}")
        now = self.env.ledger().timestamp()

        if now < lock["end"]:
            raise ContractError.LOCK_NOT_EXPIRED

        amount = lock["amount"]
        
        # Clear lock
        self.storage.remove(f"user_lock:{user}")
        self.storage.set(f"lock_exists:{user}", False)

        old_lock = {
            "amount": amount,
            "end": lock["end"],
        }
        empty_lock = {
            "amount": U128(0),
            "end": U64(0),
        }

        self._checkpoint(user, old_lock, empty_lock)

        # Transfer back to user
        token = self.storage.get("token")
        self.env.transfer(self.env.current_contract(), user, token, amount)

        self.env.emit_event("withdrawn", {
            "user": user,
            "amount": amount,
        })

    # ── Views ────────────────────────────────────────────────────────────

    @view
    def get_voting_power(self, user: Address, timestamp: U64) -> U128:
        """
        Get the voting power of a user at a specific timestamp.
        Uses binary search over user checkpoints.
        """
        count = self.storage.get(f"user_checkpoint_count:{user}", U64(0))
        if count == U64(0):
            return U128(0)

        # Binary search user checkpoints
        idx = self._find_user_checkpoint(user, timestamp, count)
        checkpoint = self.storage.get(f"user_checkpoint:{user}:{idx}")
        
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
    def get_total_voting_power(self, timestamp: U64) -> U128:
        """
        Get the total voting power across all locked tokens at a timestamp.
        Uses binary search over global checkpoints.
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
    def get_lock(self, user: Address) -> Map:
        """Return lock details for a user."""
        if not self.storage.get(f"lock_exists:{user}", False):
            raise ContractError.LOCK_NOT_FOUND
        return self.storage.get(f"user_lock:{user}")

    # ── Checkpoint & Math Helpers ────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _find_user_checkpoint(self, user: Address, timestamp: U64, count: U64) -> U64:
        low = U64(0)
        high = count - U64(1)

        while low < high:
            mid = (low + high + U64(1)) / U64(2)
            mid_checkpoint = self.storage.get(f"user_checkpoint:{user}:{mid}")
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

    def _checkpoint(
        self,
        user: Address,
        old_lock: Map or None,
        new_lock: Map
    ):
        """
        Record voting power checkpoint for both the user and globally.
        """
        now = self.env.ledger().timestamp()

        # 1. Update user checkpoint
        user_slope = U128(0)
        user_bias = U128(0)

        if new_lock["amount"] > U128(0) and new_lock["end"] > now:
            user_slope = (new_lock["amount"] * PRECISION) / U128(MAX_TIME)
            dt = U128(new_lock["end"] - now)
            user_bias = (user_slope * dt) / PRECISION

        user_count = self.storage.get(f"user_checkpoint_count:{user}", U64(0))
        user_checkpoint = {
            "timestamp": now,
            "bias": user_bias,
            "slope": user_slope,
        }
        
        self.storage.set(f"user_checkpoint:{user}:{user_count}", user_checkpoint)
        self.storage.set(f"user_checkpoint_count:{user}", user_count + U64(1))

        # 2. Update slope changes and global checkpoints
        # Subtract old lock's future slope reductions
        if old_lock is not None and old_lock["amount"] > U128(0):
            old_slope = (old_lock["amount"] * PRECISION) / U128(MAX_TIME)
            old_end = old_lock["end"]
            current_change = self.storage.get(f"slope_change:{old_end}", U128(0))
            if current_change >= old_slope:
                self.storage.set(f"slope_change:{old_end}", current_change - old_slope)
            else:
                self.storage.set(f"slope_change:{old_end}", U128(0))

        # Add new lock's future slope reductions
        if new_lock["amount"] > U128(0) and new_lock["end"] > now:
            new_slope = (new_lock["amount"] * PRECISION) / U128(MAX_TIME)
            new_end = new_lock["end"]
            current_change = self.storage.get(f"slope_change:{new_end}", U128(0))
            self.storage.set(f"slope_change:{new_end}", current_change + new_slope)

        self._checkpoint_global(now, old_lock, new_lock)

    def _checkpoint_global(self, now: U64, old_lock: Map or None, new_lock: Map):
        """
        Update the global state by stepping through time, applying week-by-week
        slope decays to the global bias/slope.
        """
        last_checkpoint_time = self.storage.get("last_global_checkpoint_time")
        global_count = self.storage.get("global_checkpoint_count")
        last_checkpoint = self.storage.get(f"global_checkpoint:{global_count - U64(1)}")

        last_bias = last_checkpoint["bias"]
        last_slope = last_checkpoint["slope"]

        # Step forward week-by-week
        t = (last_checkpoint_time / WEEK) * WEEK
        
        while t < now:
            t += WEEK
            dt = WEEK
            if t > now:
                dt = now - (t - WEEK)
                t = now

            # Decay bias
            decay = (last_slope * U128(dt)) / PRECISION
            if last_bias > decay:
                last_bias -= decay
            else:
                last_bias = U128(0)

            # Apply slope change at the week boundary
            if t % WEEK == U64(0):
                change = self.storage.get(f"slope_change:{t}", U128(0))
                if last_slope > change:
                    last_slope -= change
                else:
                    last_slope = U128(0)

            # Record checkpoint
            new_global_checkpoint = {
                "timestamp": t,
                "bias": last_bias,
                "slope": last_slope,
            }
            self.storage.set(f"global_checkpoint:{global_count}", new_global_checkpoint)
            global_count += U64(1)

        # Apply the current change delta between old_lock and new_lock
        # Old lock slope & bias subtraction
        if old_lock is not None and old_lock["amount"] > U128(0):
            old_slope = (old_lock["amount"] * PRECISION) / U128(MAX_TIME)
            old_dt = U128(old_lock["end"] - now) if old_lock["end"] > now else U128(0)
            old_bias = (old_slope * old_dt) / PRECISION
            
            if last_slope > old_slope:
                last_slope -= old_slope
            else:
                last_slope = U128(0)
                
            if last_bias > old_bias:
                last_bias -= old_bias
            else:
                last_bias = U128(0)

        # New lock slope & bias addition
        if new_lock["amount"] > U128(0) and new_lock["end"] > now:
            new_slope = (new_lock["amount"] * PRECISION) / U128(MAX_TIME)
            new_dt = U128(new_lock["end"] - now)
            new_bias = (new_slope * new_dt) / PRECISION

            last_slope += new_slope
            last_bias += new_bias

        # Save final state checkpoint
        final_global_checkpoint = {
            "timestamp": now,
            "bias": last_bias,
            "slope": last_slope,
        }
        self.storage.set(f"global_checkpoint:{global_count - U64(1)}", final_global_checkpoint)
        self.storage.set("last_global_checkpoint_time", now)
        self.storage.set("global_checkpoint_count", global_count)
