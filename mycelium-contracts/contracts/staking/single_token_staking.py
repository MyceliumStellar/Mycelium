"""
SingleTokenStaking — Stake-to-earn with configurable reward rates and lock periods.

Mycelium Smart Contract for Stellar

Features:
- Stake/unstake with configurable cooldown periods
- Reward rate per second with continuous accrual tracking per user
- Configurable lock periods (7/30/90/180 days) with multiplier bonuses
- Early unstake penalty (percentage of staked amount)
- Admin reward pool top-up
- Emergency withdraw (forfeits all pending rewards)
- Total staked tracking with global reward index
- Precision-safe reward calculations using scaled integers
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
    INSUFFICIENT_STAKE = 5
    LOCK_NOT_EXPIRED = 6
    COOLDOWN_ACTIVE = 7
    NO_REWARDS = 8
    INVALID_LOCK_PERIOD = 9
    INSUFFICIENT_REWARD_POOL = 10
    NOTHING_STAKED = 11
    INVALID_REWARD_RATE = 12
    OVERFLOW = 13
    STAKE_POSITION_NOT_FOUND = 14
    PENALTY_EXCEEDS_STAKE = 15
    MAX_POSITIONS_REACHED = 16


# ── Constants ────────────────────────────────────────────────────────────────

PRECISION = U128(1_000_000_000_000)  # 1e12 for reward-per-token scaling
SECONDS_PER_DAY = U64(86400)
MAX_POSITIONS_PER_USER = U64(20)

LOCK_7_DAYS = U64(7)
LOCK_30_DAYS = U64(30)
LOCK_90_DAYS = U64(90)
LOCK_180_DAYS = U64(180)

# Multiplier basis points: 10000 = 1.0x
MULTIPLIER_7 = U64(10000)    # 1.0x
MULTIPLIER_30 = U64(12500)   # 1.25x
MULTIPLIER_90 = U64(17500)   # 1.75x
MULTIPLIER_180 = U64(25000)  # 2.5x

EARLY_UNSTAKE_PENALTY_BPS = U64(1500)  # 15 %
COOLDOWN_DURATION = U64(86400)          # 24 hours


@contract
class SingleTokenStaking:
    """
    A single-token staking contract where users lock tokens for a chosen
    period and earn rewards proportional to their weighted stake.

    The global reward index grows every second based on the configured
    reward-per-second rate and the total effective (weighted) stake.
    Each user snapshot tracks the index at last interaction so the
    difference × user weight gives accrued rewards.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ── Admin / Lifecycle ────────────────────────────────────────────────

    @external
    def initialize(
        self,
        admin: Address,
        staking_token: Address,
        reward_token: Address,
        reward_per_second: U128,
    ):
        """
        One-time initialisation.  Sets the admin, token addresses and the
        base reward emission rate.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        if reward_per_second == U128(0):
            raise ContractError.INVALID_REWARD_RATE

        self.storage.set("admin", admin)
        self.storage.set("staking_token", staking_token)
        self.storage.set("reward_token", reward_token)
        self.storage.set("reward_per_second", reward_per_second)
        self.storage.set("total_effective_stake", U128(0))
        self.storage.set("total_raw_stake", U128(0))
        self.storage.set("reward_pool", U128(0))
        self.storage.set("global_reward_index", U128(0))
        self.storage.set("last_update_time", self.env.ledger().timestamp())
        self.storage.set("total_rewards_distributed", U128(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "staking_token": staking_token,
            "reward_token": reward_token,
            "reward_per_second": reward_per_second,
        })

    @external
    def set_reward_rate(self, caller: Address, new_rate: U128):
        """Admin-only: update the reward emission rate after settling accruals."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        if new_rate == U128(0):
            raise ContractError.INVALID_REWARD_RATE

        self._update_global_index()

        old_rate = self.storage.get("reward_per_second")
        self.storage.set("reward_per_second", new_rate)

        self.env.emit_event("reward_rate_updated", {
            "old_rate": old_rate,
            "new_rate": new_rate,
        })

    @external
    def top_up_rewards(self, caller: Address, amount: U128):
        """Admin deposits additional reward tokens into the reward pool."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        reward_token = self.storage.get("reward_token")
        self.env.transfer(caller, self.env.current_contract(), reward_token, amount)

        current_pool = self.storage.get("reward_pool")
        self.storage.set("reward_pool", current_pool + amount)

        self.env.emit_event("rewards_topped_up", {
            "by": caller,
            "amount": amount,
            "new_pool": current_pool + amount,
        })

    # ── Staking ──────────────────────────────────────────────────────────

    @external
    def stake(self, user: Address, amount: U128, lock_days: U64):
        """
        Create a new stake position with a chosen lock period.
        Transfers `amount` of staking token from the user.
        """
        user.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        multiplier = self._lock_multiplier(lock_days)
        self._update_global_index()
        self._settle_user_rewards(user)

        staking_token = self.storage.get("staking_token")
        self.env.transfer(user, self.env.current_contract(), staking_token, amount)

        # Build position
        now = self.env.ledger().timestamp()
        lock_end = now + U64(lock_days) * SECONDS_PER_DAY
        position_id = self._next_position_id(user)

        if position_id >= MAX_POSITIONS_PER_USER:
            raise ContractError.MAX_POSITIONS_REACHED

        effective_amount = (amount * U128(multiplier)) / U128(10000)

        position = {
            "amount": amount,
            "effective": effective_amount,
            "lock_end": lock_end,
            "lock_days": lock_days,
            "staked_at": now,
            "cooldown_start": U64(0),
        }
        self.storage.set(f"position:{user}:{position_id}", position)
        self.storage.set(f"position_count:{user}", position_id + U64(1))

        # Update totals
        total_eff = self.storage.get("total_effective_stake")
        total_raw = self.storage.get("total_raw_stake")
        self.storage.set("total_effective_stake", total_eff + effective_amount)
        self.storage.set("total_raw_stake", total_raw + amount)

        self.env.emit_event("staked", {
            "user": user,
            "amount": amount,
            "effective": effective_amount,
            "lock_days": lock_days,
            "lock_end": lock_end,
            "position_id": position_id,
        })

    @external
    def initiate_cooldown(self, user: Address, position_id: U64):
        """Begin the cooldown period before unstaking a position."""
        user.require_auth()
        self._require_initialized()

        position = self._get_position(user, position_id)
        now = self.env.ledger().timestamp()

        if now < position["lock_end"]:
            raise ContractError.LOCK_NOT_EXPIRED

        if position["cooldown_start"] != U64(0):
            raise ContractError.COOLDOWN_ACTIVE

        position["cooldown_start"] = now
        self.storage.set(f"position:{user}:{position_id}", position)

        self.env.emit_event("cooldown_initiated", {
            "user": user,
            "position_id": position_id,
            "cooldown_end": now + COOLDOWN_DURATION,
        })

    @external
    def unstake(self, user: Address, position_id: U64):
        """
        Withdraw a staked position after both lock and cooldown have elapsed.
        Automatically claims pending rewards.
        """
        user.require_auth()
        self._require_initialized()

        self._update_global_index()
        self._settle_user_rewards(user)

        position = self._get_position(user, position_id)
        now = self.env.ledger().timestamp()

        if now < position["lock_end"]:
            raise ContractError.LOCK_NOT_EXPIRED

        cooldown_start = position["cooldown_start"]
        if cooldown_start == U64(0):
            raise ContractError.COOLDOWN_ACTIVE  # not yet initiated

        if now < cooldown_start + COOLDOWN_DURATION:
            raise ContractError.COOLDOWN_ACTIVE

        amount = position["amount"]
        effective = position["effective"]

        # Return tokens
        staking_token = self.storage.get("staking_token")
        self.env.transfer(self.env.current_contract(), user, staking_token, amount)

        # Update totals
        total_eff = self.storage.get("total_effective_stake")
        total_raw = self.storage.get("total_raw_stake")
        self.storage.set("total_effective_stake", total_eff - effective)
        self.storage.set("total_raw_stake", total_raw - amount)

        # Remove position
        self._remove_position(user, position_id)

        self.env.emit_event("unstaked", {
            "user": user,
            "amount": amount,
            "position_id": position_id,
        })

    @external
    def early_unstake(self, user: Address, position_id: U64):
        """
        Unstake before lock expiry.  Incurs an early-withdrawal penalty
        (percentage of principal) and forfeits all accumulated rewards
        for this position.
        """
        user.require_auth()
        self._require_initialized()

        self._update_global_index()

        position = self._get_position(user, position_id)
        amount = position["amount"]
        effective = position["effective"]

        penalty = (amount * U128(EARLY_UNSTAKE_PENALTY_BPS)) / U128(10000)
        payout = amount - penalty

        if payout == U128(0):
            raise ContractError.PENALTY_EXCEEDS_STAKE

        staking_token = self.storage.get("staking_token")
        self.env.transfer(self.env.current_contract(), user, staking_token, payout)

        # Penalty stays in the contract (could be sent to treasury)
        total_eff = self.storage.get("total_effective_stake")
        total_raw = self.storage.get("total_raw_stake")
        self.storage.set("total_effective_stake", total_eff - effective)
        self.storage.set("total_raw_stake", total_raw - amount)

        self._remove_position(user, position_id)

        self.env.emit_event("early_unstaked", {
            "user": user,
            "amount": amount,
            "penalty": penalty,
            "payout": payout,
            "position_id": position_id,
        })

    @external
    def emergency_withdraw(self, user: Address):
        """
        Withdraw ALL positions immediately, forfeiting all pending rewards.
        No lock or cooldown enforced.
        """
        user.require_auth()
        self._require_initialized()
        self._update_global_index()

        count = self.storage.get(f"position_count:{user}", U64(0))
        if count == U64(0):
            raise ContractError.NOTHING_STAKED

        total_return = U128(0)
        total_eff_removed = U128(0)
        staking_token = self.storage.get("staking_token")

        for i in range(count):
            key = f"position:{user}:{i}"
            position = self.storage.get(key, None)
            if position is not None:
                total_return += position["amount"]
                total_eff_removed += position["effective"]
                self.storage.remove(key)

        self.storage.set(f"position_count:{user}", U64(0))

        # Reset user reward state — rewards forfeited
        self.storage.set(f"user_reward_index:{user}", self.storage.get("global_reward_index"))
        self.storage.set(f"user_accrued:{user}", U128(0))

        if total_return > U128(0):
            self.env.transfer(self.env.current_contract(), user, staking_token, total_return)

        total_eff = self.storage.get("total_effective_stake")
        total_raw = self.storage.get("total_raw_stake")
        self.storage.set("total_effective_stake", total_eff - total_eff_removed)
        self.storage.set("total_raw_stake", total_raw - total_return)

        self.env.emit_event("emergency_withdraw", {
            "user": user,
            "total_returned": total_return,
            "rewards_forfeited": True,
        })

    # ── Rewards ──────────────────────────────────────────────────────────

    @external
    def claim_rewards(self, user: Address):
        """Claim all accrued rewards for the caller."""
        user.require_auth()
        self._require_initialized()

        self._update_global_index()
        self._settle_user_rewards(user)

        accrued = self.storage.get(f"user_accrued:{user}", U128(0))
        if accrued == U128(0):
            raise ContractError.NO_REWARDS

        reward_pool = self.storage.get("reward_pool")
        payout = accrued if accrued <= reward_pool else reward_pool

        if payout == U128(0):
            raise ContractError.INSUFFICIENT_REWARD_POOL

        reward_token = self.storage.get("reward_token")
        self.env.transfer(self.env.current_contract(), user, reward_token, payout)

        self.storage.set("reward_pool", reward_pool - payout)
        self.storage.set(f"user_accrued:{user}", accrued - payout)

        distributed = self.storage.get("total_rewards_distributed")
        self.storage.set("total_rewards_distributed", distributed + payout)

        self.env.emit_event("rewards_claimed", {
            "user": user,
            "amount": payout,
        })

    # ── Views ────────────────────────────────────────────────────────────

    @view
    def get_pending_rewards(self, user: Address) -> U128:
        """Return the total unclaimed rewards for a user (real-time estimate)."""
        total_eff = self.storage.get("total_effective_stake", U128(0))
        if total_eff == U128(0):
            return self.storage.get(f"user_accrued:{user}", U128(0))

        now = self.env.ledger().timestamp()
        last = self.storage.get("last_update_time", now)
        elapsed = U128(now - last)
        rps = self.storage.get("reward_per_second", U128(0))
        new_rewards = rps * elapsed
        index = self.storage.get("global_reward_index", U128(0))
        index += (new_rewards * PRECISION) / total_eff

        user_index = self.storage.get(f"user_reward_index:{user}", U128(0))
        user_eff = self._total_user_effective(user)
        pending = (user_eff * (index - user_index)) / PRECISION
        accrued = self.storage.get(f"user_accrued:{user}", U128(0))
        return accrued + pending

    @view
    def get_position(self, user: Address, position_id: U64) -> Map:
        """Return a single stake position's details."""
        return self._get_position(user, position_id)

    @view
    def get_position_count(self, user: Address) -> U64:
        """Return how many positions a user has."""
        return self.storage.get(f"position_count:{user}", U64(0))

    @view
    def get_total_staked(self) -> U128:
        """Return raw (un-weighted) total staked across all users."""
        return self.storage.get("total_raw_stake", U128(0))

    @view
    def get_total_effective_stake(self) -> U128:
        """Return the weighted total effective stake."""
        return self.storage.get("total_effective_stake", U128(0))

    @view
    def get_reward_pool_balance(self) -> U128:
        """Remaining rewards in pool."""
        return self.storage.get("reward_pool", U128(0))

    @view
    def get_contract_info(self) -> Map:
        """Return high-level contract parameters."""
        return {
            "staking_token": self.storage.get("staking_token"),
            "reward_token": self.storage.get("reward_token"),
            "reward_per_second": self.storage.get("reward_per_second"),
            "total_raw_stake": self.storage.get("total_raw_stake"),
            "total_effective_stake": self.storage.get("total_effective_stake"),
            "reward_pool": self.storage.get("reward_pool"),
            "total_rewards_distributed": self.storage.get("total_rewards_distributed"),
        }

    # ── Internal Helpers ─────────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _lock_multiplier(self, lock_days: U64) -> U64:
        """Return the bonus multiplier (in basis points) for a lock period."""
        if lock_days == LOCK_7_DAYS:
            return MULTIPLIER_7
        if lock_days == LOCK_30_DAYS:
            return MULTIPLIER_30
        if lock_days == LOCK_90_DAYS:
            return MULTIPLIER_90
        if lock_days == LOCK_180_DAYS:
            return MULTIPLIER_180
        raise ContractError.INVALID_LOCK_PERIOD

    def _update_global_index(self):
        """
        Advance the global reward-per-effective-token index up to the
        current timestamp.  If total effective stake is zero the index
        stays flat (rewards do not accrue to nobody).
        """
        now = self.env.ledger().timestamp()
        last = self.storage.get("last_update_time")
        if now <= last:
            return

        total_eff = self.storage.get("total_effective_stake")
        if total_eff > U128(0):
            elapsed = U128(now - last)
            rps = self.storage.get("reward_per_second")
            new_rewards = rps * elapsed
            index = self.storage.get("global_reward_index")
            index += (new_rewards * PRECISION) / total_eff
            self.storage.set("global_reward_index", index)

        self.storage.set("last_update_time", now)

    def _settle_user_rewards(self, user: Address):
        """
        Snapshot accrued rewards for *user* based on the delta between the
        global index and the user's last-seen index, multiplied by the
        user's total effective stake.
        """
        index = self.storage.get("global_reward_index")
        user_index = self.storage.get(f"user_reward_index:{user}", U128(0))

        if index > user_index:
            user_eff = self._total_user_effective(user)
            if user_eff > U128(0):
                delta = index - user_index
                new_accrued = (user_eff * delta) / PRECISION
                old_accrued = self.storage.get(f"user_accrued:{user}", U128(0))
                self.storage.set(f"user_accrued:{user}", old_accrued + new_accrued)

        self.storage.set(f"user_reward_index:{user}", index)

    def _total_user_effective(self, user: Address) -> U128:
        """Sum the effective stake across all of a user's positions."""
        count = self.storage.get(f"position_count:{user}", U64(0))
        total = U128(0)
        for i in range(count):
            pos = self.storage.get(f"position:{user}:{i}", None)
            if pos is not None:
                total += pos["effective"]
        return total

    def _get_position(self, user: Address, position_id: U64) -> Map:
        position = self.storage.get(f"position:{user}:{position_id}", None)
        if position is None:
            raise ContractError.STAKE_POSITION_NOT_FOUND
        return position

    def _next_position_id(self, user: Address) -> U64:
        return self.storage.get(f"position_count:{user}", U64(0))

    def _remove_position(self, user: Address, position_id: U64):
        """
        Remove a position by swapping with the last entry to keep the
        array compact.
        """
        count = self.storage.get(f"position_count:{user}", U64(0))
        last = count - U64(1)
        if position_id != last:
            last_pos = self.storage.get(f"position:{user}:{last}")
            self.storage.set(f"position:{user}:{position_id}", last_pos)
        self.storage.remove(f"position:{user}:{last}")
        self.storage.set(f"position_count:{user}", last)
