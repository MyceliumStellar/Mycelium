"""
Staking Airdrop — Staking during pre-launch period, airdrop allocation multipliers, daily snapshots registry, claim mechanism.

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
    STAKING_NOT_ACTIVE = 4
    STAKING_ENDED = 5
    ZERO_AMOUNT = 6
    LOCK_ACTIVE = 7
    DEPOSIT_NOT_FOUND = 8
    CLAIM_NOT_ACTIVE = 9
    ALREADY_CLAIMED = 10
    NO_REWARDS = 11
    INVALID_TIER = 12
    INVALID_TIME_RANGE = 13
    ZERO_REWARDS = 14

@contract
class StakingAirdrop:
    """A staking airdrop contract where users earn allocation points based on stake duration and multipliers."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        staking_token: Address,
        airdrop_token: Address,
        staking_start: U64,
        staking_end: U64,
        claim_start: U64,
    ):
        """Initialize the staking airdrop contract.

        Args:
            admin: Admin address.
            staking_token: Token users stake.
            airdrop_token: Token distributed as airdrop rewards.
            staking_start: Timestamp when staking begins.
            staking_end: Timestamp when staking ends and point generation stops.
            claim_start: Timestamp when airdrop rewards can be claimed.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if staking_start >= staking_end or staking_end > claim_start:
            raise ContractError.INVALID_TIME_RANGE

        self.storage.set("admin", admin)
        self.storage.set("staking_token", staking_token)
        self.storage.set("airdrop_token", airdrop_token)
        self.storage.set("staking_start", staking_start)
        self.storage.set("staking_end", staking_end)
        self.storage.set("claim_start", claim_start)

        self.storage.set("current_day", U32(0))
        self.storage.set("last_snapshot_time", staking_start)
        self.storage.set("total_staked", U128(0))
        self.storage.set("total_weighted_staked", U128(0))
        self.storage.set("total_global_points", U128(0))
        
        self.storage.set("total_airdrop_pool", U128(0))
        self.storage.set("next_stake_id", U64(0))
        self.storage.set("initialized", True)

        # Set default tiers
        # Tier 0: No lock, 1.0x (10000 bps)
        # Tier 1: 30 days lock (2592000s), 1.5x (15000 bps)
        # Tier 2: 90 days lock (7776000s), 2.0x (20000 bps)
        self._set_tier_config(0, U64(0), 10000)
        self._set_tier_config(1, U64(2592000), 15000)
        self._set_tier_config(2, U64(7776000), 20000)

        self.env.emit_event("initialized", {
            "staking_token": staking_token,
            "airdrop_token": airdrop_token,
            "staking_start": staking_start,
            "staking_end": staking_end,
        })

    @external
    def fund_airdrop_pool(self, admin: Address, amount: U128):
        """Fund the airdrop rewards pool with airdrop tokens. (Admin only)

        Args:
            admin: Admin address.
            amount: Airdrop token amount.
        """
        self._require_initialized()
        admin.require_auth()
        self._require_admin(admin)

        if amount == 0:
            raise ContractError.ZERO_AMOUNT

        airdrop_token = self.storage.get("airdrop_token")
        self.env.invoke_contract(
            airdrop_token,
            "transfer",
            [admin, self.env.current_contract_address(), amount]
        )

        pool = self.storage.get("total_airdrop_pool")
        self.storage.set("total_airdrop_pool", pool + amount)

        self.env.emit_event("airdrop_pool_funded", {"amount": amount})

    @external
    def update_tier(self, admin: Address, tier_id: U32, lock_duration: U64, multiplier: U32):
        """Configure or update a lock tier multiplier. (Admin only)

        Args:
            admin: Admin address.
            tier_id: Staking tier ID.
            lock_duration: Staking lockup duration in seconds.
            multiplier: Allocation multiplier in basis points.
        """
        self._require_initialized()
        admin.require_auth()
        self._require_admin(admin)

        self._set_tier_config(tier_id, lock_duration, multiplier)

    @external
    def stake(self, caller: Address, amount: U128, tier_id: U32) -> U64:
        """Stake tokens to earn airdrop points.

        Args:
            caller: Account staking tokens.
            amount: Amount of staking tokens.
            tier_id: Lock duration tier.
        """
        self._require_initialized()
        caller.require_auth()

        now = self.env.ledger().timestamp()
        staking_start = self.storage.get("staking_start")
        staking_end = self.storage.get("staking_end")

        if now < staking_start or now >= staking_end:
            raise ContractError.STAKING_NOT_ACTIVE

        if amount == 0:
            raise ContractError.ZERO_AMOUNT

        if not self.storage.get(("tier_exists", tier_id), False):
            raise ContractError.INVALID_TIER

        # Process snapshots catch-up
        self._catch_up_snapshots(now)

        lock_duration = self.storage.get(("tier_duration", tier_id))
        multiplier = self.storage.get(("tier_multiplier", tier_id))
        
        weighted_amount = (amount * U128(multiplier)) / U128(10000)

        # Transfer staking tokens
        staking_token = self.storage.get("staking_token")
        self.env.invoke_contract(
            staking_token,
            "transfer",
            [caller, self.env.current_contract_address(), amount]
        )

        current_day = self.storage.get("current_day")
        stake_id = self.storage.get("next_stake_id")
        self.storage.set("next_stake_id", stake_id + U64(1))

        # Save stake details
        self.storage.set(("stake_user", stake_id), caller)
        self.storage.set(("stake_amount", stake_id), amount)
        self.storage.set(("stake_weighted", stake_id), weighted_amount)
        self.storage.set(("stake_lock_until", stake_id), now + lock_duration)
        self.storage.set(("stake_last_update", stake_id), current_day)
        self.storage.set(("stake_points", stake_id), U128(0))
        self.storage.set(("stake_claimed", stake_id), False)
        self.storage.set(("stake_withdrawn", stake_id), False)

        # Update global stakes
        total_staked = self.storage.get("total_staked")
        total_weighted = self.storage.get("total_weighted_staked")

        self.storage.set("total_staked", total_staked + amount)
        self.storage.set("total_weighted_staked", total_weighted + weighted_amount)

        # Add to user's stake index
        count = self.storage.get(("user_stake_count", caller), U32(0))
        self.storage.set(("user_stake_id", caller, count), stake_id)
        self.storage.set(("user_stake_count", caller), count + U32(1))

        self.env.emit_event("tokens_staked", {
            "stake_id": stake_id,
            "user": caller,
            "amount": amount,
            "weighted": weighted_amount,
            "lock_until": now + lock_duration,
        })

        return stake_id

    @external
    def unstake(self, caller: Address, stake_id: U64):
        """Unstake tokens after the lock period has expired. Updates accumulated points.

        Args:
            caller: Account unstaking.
            stake_id: Staking position ID.
        """
        self._require_initialized()
        caller.require_auth()

        self._validate_stake_owner(caller, stake_id)

        if self.storage.get(("stake_withdrawn", stake_id), False):
            raise ContractError.ALREADY_CLAIMED

        now = self.env.ledger().timestamp()
        lock_until = self.storage.get(("stake_lock_until", stake_id))
        if now < lock_until:
            raise ContractError.LOCK_ACTIVE

        # Process snapshots catch-up
        self._catch_up_snapshots(now)

        # Accumulate remaining points for this stake up to today
        self._update_stake_points(stake_id)

        self.storage.set(("stake_withdrawn", stake_id), True)

        amount = self.storage.get(("stake_amount", stake_id))
        weighted_amount = self.storage.get(("stake_weighted", stake_id))

        # Deduct global stakes
        total_staked = self.storage.get("total_staked")
        total_weighted = self.storage.get("total_weighted_staked")

        self.storage.set("total_staked", total_staked - amount)
        self.storage.set("total_weighted_staked", total_weighted - weighted_amount)

        # Return staking tokens
        staking_token = self.storage.get("staking_token")
        self.env.invoke_contract(
            staking_token,
            "transfer",
            [self.env.current_contract_address(), caller, amount]
        )

        self.env.emit_event("tokens_unstaked", {
            "stake_id": stake_id,
            "user": caller,
            "amount": amount,
        })

    @external
    def claim_airdrop(self, caller: Address) -> U128:
        """Claim airdrop rewards based on total points accumulated across all user stakes.

        Args:
            caller: Staker address.
        """
        self._require_initialized()
        caller.require_auth()

        now = self.env.ledger().timestamp()
        claim_start = self.storage.get("claim_start")
        if now < claim_start:
            raise ContractError.CLAIM_NOT_ACTIVE

        # Catch up snapshots to end of staking period
        staking_end = self.storage.get("staking_end")
        self._catch_up_snapshots(staking_end)

        user_stake_count = self.storage.get(("user_stake_count", caller), U32(0))
        total_user_points = U128(0)
        unclaimed_stakes_count = 0

        for i in range(user_stake_count):
            stake_id = self.storage.get(("user_stake_id", caller, i))
            if not self.storage.get(("stake_claimed", stake_id), False):
                self._update_stake_points(stake_id)
                points = self.storage.get(("stake_points", stake_id))
                total_user_points += points
                self.storage.set(("stake_claimed", stake_id), True)
                unclaimed_stakes_count += 1

        if unclaimed_stakes_count == 0:
            raise ContractError.ALREADY_CLAIMED

        if total_user_points == 0:
            raise ContractError.ZERO_REWARDS

        total_global_points = self.storage.get("total_global_points")
        if total_global_points == 0:
            raise ContractError.ZERO_REWARDS

        total_pool = self.storage.get("total_airdrop_pool")
        reward_amount = (total_user_points * total_pool) / total_global_points

        if reward_amount == 0:
            raise ContractError.ZERO_REWARDS

        airdrop_token = self.storage.get("airdrop_token")
        self.env.invoke_contract(
            airdrop_token,
            "transfer",
            [self.env.current_contract_address(), caller, reward_amount]
        )

        self.env.emit_event("airdrop_claimed", {
            "user": caller,
            "points": total_user_points,
            "reward_amount": reward_amount,
        })

        return reward_amount

    @external
    def trigger_snapshot(self):
        """Public endpoint allowing anyone to trigger/catch-up pending daily snapshots."""
        self._require_initialized()
        now = self.env.ledger().timestamp()
        self._catch_up_snapshots(now)

    @view
    def get_snapshot(self, day: U32) -> Map:
        """Get staked details of a specific day snapshot.

        Args:
            day: Day number.
        """
        res = Map()
        res.set("total_staked", self.storage.get(("snapshot_total_staked", day), U128(0)))
        res.set("global_points", self.storage.get(("snapshot_global_points", day), U128(0)))
        return res

    @view
    def get_user_points(self, user: Address) -> U128:
        """Estimate current points accumulated by a user.

        Args:
            user: User address.
        """
        now = self.env.ledger().timestamp()
        staking_end = self.storage.get("staking_end")
        end_time = now if now < staking_end else staking_end

        staking_start = self.storage.get("staking_start")
        if end_time <= staking_start:
            return U128(0)

        # Estimate days elapsed
        current_day = U32((end_time - staking_start) / 86400)

        count = self.storage.get(("user_stake_count", user), U32(0))
        total_points = U128(0)

        for i in range(count):
            stake_id = self.storage.get(("user_stake_id", user, i))
            points = self.storage.get(("stake_points", stake_id), U128(0))
            
            # Add pending points
            withdrawn = self.storage.get(("stake_withdrawn", stake_id), False)
            if not withdrawn:
                last_update = self.storage.get(("stake_last_update", stake_id))
                if current_day > last_update:
                    weighted = self.storage.get(("stake_weighted", stake_id))
                    days_diff = current_day - last_update
                    points += weighted * U128(days_diff)

            total_points += points

        return total_points

    @view
    def get_info(self) -> Map:
        """Retrieve staking status details."""
        res = Map()
        res.set("staking_start", self.storage.get("staking_start"))
        res.set("staking_end", self.storage.get("staking_end"))
        res.set("claim_start", self.storage.get("claim_start"))
        res.set("current_day", self.storage.get("current_day"))
        res.set("total_staked", self.storage.get("total_staked"))
        res.set("total_global_points", self.storage.get("total_global_points"))
        res.set("total_airdrop_pool", self.storage.get("total_airdrop_pool"))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _validate_stake_owner(self, caller: Address, stake_id: U64):
        owner = self.storage.get(("stake_user", stake_id))
        if not owner:
            raise ContractError.DEPOSIT_NOT_FOUND
        if owner != caller:
            raise ContractError.UNAUTHORIZED

    def _set_tier_config(self, tier_id: U32, duration: U64, multiplier: U32):
        self.storage.set(("tier_exists", tier_id), True)
        self.storage.set(("tier_duration", tier_id), duration)
        self.storage.set(("tier_multiplier", tier_id), multiplier)

        self.env.emit_event("tier_updated", {
            "tier_id": tier_id,
            "duration": duration,
            "multiplier": multiplier,
        })

    def _catch_up_snapshots(self, timestamp: U64):
        staking_start = self.storage.get("staking_start")
        staking_end = self.storage.get("staking_end")
        
        # Calculate limit for snapshot generation (capped at staking_end)
        limit_time = timestamp if timestamp < staking_end else staking_end

        if limit_time <= staking_start:
            return

        last_snapshot = self.storage.get("last_snapshot_time")
        if limit_time <= last_snapshot:
            return

        elapsed = limit_time - last_snapshot
        days_to_add = elapsed / 86400

        if days_to_add > 0:
            current_day = self.storage.get("current_day")
            total_staked = self.storage.get("total_staked")
            total_weighted = self.storage.get("total_weighted_staked")
            total_points = self.storage.get("total_global_points")

            for i in range(int(days_to_add)):
                day = current_day + U32(i)
                self.storage.set(("snapshot_total_staked", day), total_staked)
                
                # Each day add daily weighted sum to points registry
                total_points += total_weighted
                self.storage.set(("snapshot_global_points", day), total_points)

            self.storage.set("current_day", current_day + U32(days_to_add))
            self.storage.set("total_global_points", total_points)
            self.storage.set("last_snapshot_time", last_snapshot + U64(days_to_add * 86400))

            self.env.emit_event("snapshots_updated", {
                "new_day": current_day + U32(days_to_add),
                "total_global_points": total_points,
            })

    def _update_stake_points(self, stake_id: U64):
        current_day = self.storage.get("current_day")
        last_update = self.storage.get(("stake_last_update", stake_id))

        if current_day > last_update:
            weighted = self.storage.get(("stake_weighted", stake_id))
            days_diff = current_day - last_update
            
            points_earned = weighted * U128(days_diff)
            accumulated = self.storage.get(("stake_points", stake_id))

            self.storage.set(("stake_points", stake_id), accumulated + points_earned)
            self.storage.set(("stake_last_update", stake_id), current_day)
