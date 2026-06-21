"""
BoostStaking — Reward staking contract with voting-power-based multipliers.

Mycelium Smart Contract for Stellar
Stakers receive rewards based on their "working balance" (1x to 4x of raw stake).
Working balance is determined by the staker's proportion of total veToken voting power.
Includes automatic decay update mechanisms and the ability to kick decayed users.
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
    NO_REWARDS = 6
    KICK_NOT_APPLICABLE = 7


# ── Constants ────────────────────────────────────────────────────────────────

PRECISION = U128(1_000_000_000_000)  # 1e12 for reward calculations


@contract
class BoostStaking:
    """
    Reward distribution contract where a user's share of rewards is boosted
    by their veToken holdings, up to a 4x multiplier.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ── Admin & Lifecycle ────────────────────────────────────────────────

    @external
    def initialize(
        self,
        admin: Address,
        staking_token: Address,
        reward_token: Address,
        ve_token_contract: Address,
        reward_rate: U128,
    ):
        """
        One-time initialization of contract state.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("staking_token", staking_token)
        self.storage.set("reward_token", reward_token)
        self.storage.set("ve_token", ve_token_contract)
        self.storage.set("reward_rate", reward_rate)
        self.storage.set("reward_pool", U128(0))
        
        self.storage.set("total_raw_supply", U128(0))
        self.storage.set("total_working_supply", U128(0))
        self.storage.set("global_reward_index", U128(0))
        self.storage.set("last_update_time", self.env.ledger().timestamp())
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "ve_token": ve_token_contract,
            "reward_rate": reward_rate,
        })

    @external
    def set_reward_rate(self, caller: Address, new_rate: U128):
        """
        Admin-only: update reward emission rate.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        self._update_global_index()

        old_rate = self.storage.get("reward_rate")
        self.storage.set("reward_rate", new_rate)

        self.env.emit_event("reward_rate_updated", {
            "old_rate": old_rate,
            "new_rate": new_rate,
        })

    @external
    def top_up_rewards(self, caller: Address, amount: U128):
        """
        Admin-only: Deposit reward tokens to the pool.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        reward_token = self.storage.get("reward_token")
        self.env.transfer(caller, self.env.current_contract(), reward_token, amount)

        pool = self.storage.get("reward_pool")
        self.storage.set("reward_pool", pool + amount)

        self.env.emit_event("rewards_topped_up", {
            "amount": amount,
            "new_pool": pool + amount,
        })

    # ── User Actions ─────────────────────────────────────────────────────

    @external
    def stake(self, user: Address, amount: U128):
        """
        Stake tokens and update the user's working balance.
        """
        user.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        self._update_global_index()
        self._settle_user_rewards(user)

        # Transfer staking token
        staking_token = self.storage.get("staking_token")
        self.env.transfer(user, self.env.current_contract(), staking_token, amount)

        # Update raw stakes
        raw_bal = self.storage.get(f"raw_balance:{user}", U128(0))
        new_raw_bal = raw_bal + amount
        self.storage.set(f"raw_balance:{user}", new_raw_bal)

        total_raw = self.storage.get("total_raw_supply")
        self.storage.set("total_raw_supply", total_raw + amount)

        # Adjust working balance
        self._adjust_working_balance(user, new_raw_bal)

        self.env.emit_event("staked", {
            "user": user,
            "amount": amount,
            "new_raw_balance": new_raw_bal,
        })

    @external
    def unstake(self, user: Address, amount: U128):
        """
        Unstake tokens and update the user's working balance.
        """
        user.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        raw_bal = self.storage.get(f"raw_balance:{user}", U128(0))
        if raw_bal < amount:
            raise ContractError.INSUFFICIENT_STAKE

        self._update_global_index()
        self._settle_user_rewards(user)

        # Update raw stakes
        new_raw_bal = raw_bal - amount
        self.storage.set(f"raw_balance:{user}", new_raw_bal)

        total_raw = self.storage.get("total_raw_supply")
        self.storage.set("total_raw_supply", total_raw - amount)

        # Adjust working balance
        self._adjust_working_balance(user, new_raw_bal)

        # Transfer back
        staking_token = self.storage.get("staking_token")
        self.env.transfer(self.env.current_contract(), user, staking_token, amount)

        self.env.emit_event("unstaked", {
            "user": user,
            "amount": amount,
            "new_raw_balance": new_raw_bal,
        })

    @external
    def claim_rewards(self, user: Address):
        """
        Claim accrued rewards.
        """
        user.require_auth()
        self._require_initialized()

        self._update_global_index()
        self._settle_user_rewards(user)

        accrued = self.storage.get(f"user_accrued:{user}", U128(0))
        if accrued == U128(0):
            raise ContractError.NO_REWARDS

        pool = self.storage.get("reward_pool")
        payout = accrued if accrued <= pool else pool

        if payout == U128(0):
            raise ContractError.NO_REWARDS

        self.storage.set(f"user_accrued:{user}", accrued - payout)
        self.storage.set("reward_pool", pool - payout)

        reward_token = self.storage.get("reward_token")
        self.env.transfer(self.env.current_contract(), user, reward_token, payout)

        self.env.emit_event("rewards_claimed", {
            "user": user,
            "amount": payout,
        })

    @external
    def update_user_boost(self, user: Address):
        """
        Recalculate boost multiplier for a user. Anyone can trigger this
        to keep working balances in sync with veToken decays.
        """
        self._require_initialized()
        self._update_global_index()
        self._settle_user_rewards(user)

        raw_bal = self.storage.get(f"raw_balance:{user}", U128(0))
        self._adjust_working_balance(user, raw_bal)

    @external
    def kick(self, caller: Address, user: Address):
        """
        Kick a user whose veToken lock has decayed or expired, reducing their boost.
        Kicker is allowed if the user's actual working balance has dropped below
        their stored working balance.
        """
        caller.require_auth()
        self._require_initialized()

        self._update_global_index()
        self._settle_user_rewards(user)

        raw_bal = self.storage.get(f"raw_balance:{user}", U128(0))
        stored_working = self.storage.get(f"working_balance:{user}", U128(0))
        
        # Calculate what their working balance should be right now
        calculated_working = self._calculate_working_balance(user, raw_bal)

        # Kick is only allowed if working balance drops
        if calculated_working >= stored_working:
            raise ContractError.KICK_NOT_APPLICABLE

        self._adjust_working_balance(user, raw_bal)

        self.env.emit_event("user_kicked", {
            "user": user,
            "kicker": caller,
            "old_working": stored_working,
            "new_working": calculated_working,
        })

    # ── View Functions ───────────────────────────────────────────────────

    @view
    def get_user_info(self, user: Address) -> Map:
        """
        Get detailed user staking information.
        """
        raw_bal = self.storage.get(f"raw_balance:{user}", U128(0))
        working_bal = self.storage.get(f"working_balance:{user}", U128(0))
        
        multiplier = U64(10000)
        if raw_bal > U128(0):
            multiplier = U64((working_bal * U128(10000)) / raw_bal)

        return {
            "raw_balance": raw_bal,
            "working_balance": working_bal,
            "boost_multiplier_bps": multiplier,
            "pending_rewards": self._get_pending_rewards_estimate(user),
        }

    @view
    def get_pool_info(self) -> Map:
        """
        Get global staking pool stats.
        """
        return {
            "total_raw_supply": self.storage.get("total_raw_supply", U128(0)),
            "total_working_supply": self.storage.get("total_working_supply", U128(0)),
            "reward_rate": self.storage.get("reward_rate", U128(0)),
            "reward_pool": self.storage.get("reward_pool", U128(0)),
        }

    # ── Internal Helpers ─────────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _update_global_index(self):
        """
        Advance global index based on working supply.
        """
        now = self.env.ledger().timestamp()
        last = self.storage.get("last_update_time")
        if now <= last:
            return

        working_supply = self.storage.get("total_working_supply")
        if working_supply > U128(0):
            elapsed = U128(now - last)
            rate = self.storage.get("reward_rate")
            new_rewards = elapsed * rate
            
            index = self.storage.get("global_reward_index")
            index += (new_rewards * PRECISION) / working_supply
            self.storage.set("global_reward_index", index)

        self.storage.set("last_update_time", now)

    def _settle_user_rewards(self, user: Address):
        """
        Accrue pending rewards for user based on their working balance.
        """
        index = self.storage.get("global_reward_index")
        user_index = self.storage.get(f"user_reward_index:{user}", U128(0))

        if index > user_index:
            working_bal = self.storage.get(f"working_balance:{user}", U128(0))
            if working_bal > U128(0):
                delta = index - user_index
                accrued = (working_bal * delta) / PRECISION
                
                old_accrued = self.storage.get(f"user_accrued:{user}", U128(0))
                self.storage.set(f"user_accrued:{user}", old_accrued + accrued)

        self.storage.set(f"user_reward_index:{user}", index)

    def _calculate_working_balance(self, user: Address, raw_balance: U128) -> U128:
        """
        Calculate user working balance using voting power:
        working_balance = min(raw_balance * 4, raw_balance * 0.25 + total_raw * user_ve / total_ve * 0.75)
        """
        if raw_balance == U128(0):
            return U128(0)

        ve_token = self.storage.get("ve_token")
        now = self.env.ledger().timestamp()

        # Query external veToken contract
        user_ve = self.env.call(ve_token, "get_voting_power", [user, now])
        total_ve = self.env.call(ve_token, "get_total_voting_power", [now])

        base_balance = raw_balance / U128(4)  # 25% base

        if total_ve == U128(0) or user_ve == U128(0):
            return base_balance

        total_raw = self.storage.get("total_raw_supply")
        boost_balance = (total_raw * user_ve * U128(3)) / (total_ve * U128(4))  # 75% max boost share
        
        calculated = base_balance + boost_balance
        max_balance = raw_balance * U128(4)  # 4x cap

        return calculated if calculated < max_balance else max_balance

    def _adjust_working_balance(self, user: Address, raw_balance: U128):
        """
        Apply working balance calculation and update global working supply.
        """
        old_working = self.storage.get(f"working_balance:{user}", U128(0))
        new_working = self._calculate_working_balance(user, raw_balance)

        self.storage.set(f"working_balance:{user}", new_working)

        total_working = self.storage.get("total_working_supply")
        self.storage.set("total_working_supply", (total_working - old_working) + new_working)

    def _get_pending_rewards_estimate(self, user: Address) -> U128:
        """
        Real-time estimate of pending rewards.
        """
        working_supply = self.storage.get("total_working_supply", U128(0))
        if working_supply == U128(0):
            return self.storage.get(f"user_accrued:{user}", U128(0))

        now = self.env.ledger().timestamp()
        last = self.storage.get("last_update_time", now)
        elapsed = U128(now - last)
        rate = self.storage.get("reward_rate", U128(0))
        new_rewards = rate * elapsed

        index = self.storage.get("global_reward_index", U128(0))
        index += (new_rewards * PRECISION) / working_supply

        user_index = self.storage.get(f"user_reward_index:{user}", U128(0))
        working_bal = self.storage.get(f"working_balance:{user}", U128(0))
        pending = (working_bal * (index - user_index)) / PRECISION
        accrued = self.storage.get(f"user_accrued:{user}", U128(0))

        return accrued + pending
