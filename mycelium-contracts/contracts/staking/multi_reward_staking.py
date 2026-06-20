"""
MultiRewardStaking — Multi-reward staking contract.

Mycelium Smart Contract for Stellar
Allows users to stake a single token and earn multiple reward tokens dynamically.
The admin can add, update, and stop reward emissions for different tokens.
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
    INVALID_REWARD_TOKEN = 7
    REWARD_TOKEN_ALREADY_EXISTS = 8
    INSUFFICIENT_REWARD_POOL = 9
    NOTHING_STAKED = 10
    REWARD_TOKEN_NOT_FOUND = 11
    REWARD_TOKEN_LIMIT_REACHED = 12


# ── Constants ────────────────────────────────────────────────────────────────

PRECISION = U128(1_000_000_000_000)  # 1e12 for reward-per-token scaling
MAX_REWARD_TOKENS = U64(10)


@contract
class MultiRewardStaking:
    """
    Staking contract enabling multiple independent reward tokens to be emitted
    proproportionally to stakers over configurable durations.
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
    ):
        """
        One-time initialization. Sets the admin and the staking token.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("staking_token", staking_token)
        self.storage.set("total_staked", U128(0))
        self.storage.set("reward_tokens", Vec())
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "staking_token": staking_token,
        })

    @external
    def add_reward_token(
        self,
        caller: Address,
        reward_token: Address,
    ):
        """
        Admin-only: register a new reward token.
        Initializes reward token parameters with 0 rate.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        tokens = self.storage.get("reward_tokens")
        
        # Check limit
        if tokens.len() >= MAX_REWARD_TOKENS:
            raise ContractError.REWARD_TOKEN_LIMIT_REACHED

        # Ensure not already added
        for i in range(tokens.len()):
            if tokens.get(i) == reward_token:
                raise ContractError.REWARD_TOKEN_ALREADY_EXISTS

        tokens.append(reward_token)
        self.storage.set("reward_tokens", tokens)

        # Initialize storage for token
        self.storage.set(f"reward_rate:{reward_token}", U128(0))
        self.storage.set(f"period_finish:{reward_token}", U64(0))
        self.storage.set(f"last_update_time:{reward_token}", self.env.ledger().timestamp())
        self.storage.set(f"reward_per_token_stored:{reward_token}", U128(0))
        self.storage.set(f"reward_pool:{reward_token}", U128(0))

        self.env.emit_event("reward_token_added", {
            "reward_token": reward_token,
        })

    @external
    def set_reward_emission(
        self,
        caller: Address,
        reward_token: Address,
        amount: U128,
        duration_seconds: U64,
    ):
        """
        Admin-only: Set or extend reward emission for a registered token.
        Transfers reward tokens from admin to the reward pool.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        if not self._is_reward_token(reward_token):
            raise ContractError.REWARD_TOKEN_NOT_FOUND

        if amount == U128(0) or duration_seconds == U64(0):
            raise ContractError.ZERO_AMOUNT

        self._update_reward(reward_token, None)

        now = self.env.ledger().timestamp()
        period_finish = self.storage.get(f"period_finish:{reward_token}")

        new_rate = U128(0)
        if now >= period_finish:
            new_rate = amount / U128(duration_seconds)
        else:
            remaining = period_finish - now
            leftover = U128(remaining) * self.storage.get(f"reward_rate:{reward_token}")
            new_rate = (amount + leftover) / U128(duration_seconds)

        # Transfer rewards to contract
        self.env.transfer(caller, self.env.current_contract(), reward_token, amount)

        # Update reward pool and emission config
        current_pool = self.storage.get(f"reward_pool:{reward_token}")
        self.storage.set(f"reward_pool:{reward_token}", current_pool + amount)
        self.storage.set(f"reward_rate:{reward_token}", new_rate)
        self.storage.set(f"last_update_time:{reward_token}", now)
        self.storage.set(f"period_finish:{reward_token}", now + duration_seconds)

        self.env.emit_event("reward_emission_set", {
            "reward_token": reward_token,
            "amount": amount,
            "rate": new_rate,
            "duration": duration_seconds,
            "period_finish": now + duration_seconds,
        })

    @external
    def stop_reward_emission(self, caller: Address, reward_token: Address):
        """
        Admin-only: Stop reward emission for a token early.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        if not self._is_reward_token(reward_token):
            raise ContractError.REWARD_TOKEN_NOT_FOUND

        self._update_reward(reward_token, None)

        now = self.env.ledger().timestamp()
        self.storage.set(f"period_finish:{reward_token}", now)
        self.storage.set(f"reward_rate:{reward_token}", U128(0))
        self.storage.set(f"last_update_time:{reward_token}", now)

        self.env.emit_event("reward_emission_stopped", {
            "reward_token": reward_token,
        })

    # ── User Actions ─────────────────────────────────────────────────────

    @external
    def stake(self, user: Address, amount: U128):
        """
        Stake tokens in the contract.
        Updates user rewards prior to increasing their balance.
        """
        user.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        self._update_all_rewards(user)

        # Transfer staking token to contract
        staking_token = self.storage.get("staking_token")
        self.env.transfer(user, self.env.current_contract(), staking_token, amount)

        # Update user balance
        user_bal = self.storage.get(f"user_stake:{user}", U128(0))
        self.storage.set(f"user_stake:{user}", user_bal + amount)

        # Update total stake
        total_staked = self.storage.get("total_staked")
        self.storage.set("total_staked", total_staked + amount)

        self.env.emit_event("staked", {
            "user": user,
            "amount": amount,
            "new_stake": user_bal + amount,
        })

    @external
    def unstake(self, user: Address, amount: U128):
        """
        Unstake tokens from the contract.
        Updates user rewards prior to decreasing their balance.
        """
        user.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        user_bal = self.storage.get(f"user_stake:{user}", U128(0))
        if user_bal < amount:
            raise ContractError.INSUFFICIENT_STAKE

        self._update_all_rewards(user)

        # Update user balance
        self.storage.set(f"user_stake:{user}", user_bal - amount)

        # Update total stake
        total_staked = self.storage.get("total_staked")
        self.storage.set("total_staked", total_staked - amount)

        # Transfer staking token back to user
        staking_token = self.storage.get("staking_token")
        self.env.transfer(self.env.current_contract(), user, staking_token, amount)

        self.env.emit_event("unstaked", {
            "user": user,
            "amount": amount,
            "new_stake": user_bal - amount,
        })

    @external
    def claim_reward(self, user: Address, reward_token: Address):
        """
        Claim accrued rewards for a specific reward token.
        """
        user.require_auth()
        self._require_initialized()

        if not self._is_reward_token(reward_token):
            raise ContractError.REWARD_TOKEN_NOT_FOUND

        self._update_reward(reward_token, user)

        claimed = self.storage.get(f"user_rewards:{user}:{reward_token}", U128(0))
        if claimed == U128(0):
            raise ContractError.NO_REWARDS

        reward_pool = self.storage.get(f"reward_pool:{reward_token}")
        payout = claimed if claimed <= reward_pool else reward_pool

        if payout == U128(0):
            raise ContractError.INSUFFICIENT_REWARD_POOL

        self.storage.set(f"user_rewards:{user}:{reward_token}", claimed - payout)
        self.storage.set(f"reward_pool:{reward_token}", reward_pool - payout)

        self.env.transfer(self.env.current_contract(), user, reward_token, payout)

        self.env.emit_event("reward_claimed", {
            "user": user,
            "reward_token": reward_token,
            "amount": payout,
        })

    @external
    def claim_all(self, user: Address):
        """
        Claim rewards for all registered reward tokens.
        """
        user.require_auth()
        self._require_initialized()

        tokens = self.storage.get("reward_tokens")
        claimed_any = False

        for i in range(tokens.len()):
            token = tokens.get(i)
            self._update_reward(token, user)
            claimed = self.storage.get(f"user_rewards:{user}:{token}", U128(0))
            
            if claimed > U128(0):
                reward_pool = self.storage.get(f"reward_pool:{token}")
                payout = claimed if claimed <= reward_pool else reward_pool
                if payout > U128(0):
                    self.storage.set(f"user_rewards:{user}:{token}", claimed - payout)
                    self.storage.set(f"reward_pool:{token}", reward_pool - payout)
                    self.env.transfer(self.env.current_contract(), user, token, payout)
                    claimed_any = True
                    self.env.emit_event("reward_claimed", {
                        "user": user,
                        "reward_token": token,
                        "amount": payout,
                    })

        if not claimed_any:
            raise ContractError.NO_REWARDS

    # ── Views ────────────────────────────────────────────────────────────

    @view
    def get_user_stake(self, user: Address) -> U128:
        """Get the amount of staking token staked by a user."""
        return self.storage.get(f"user_stake:{user}", U128(0))

    @view
    def get_total_staked(self) -> U128:
        """Get total staking token staked in the contract."""
        return self.storage.get("total_staked", U128(0))

    @view
    def get_reward_tokens(self) -> Vec:
        """Get list of registered reward token addresses."""
        return self.storage.get("reward_tokens")

    @view
    def get_pending_reward(self, user: Address, reward_token: Address) -> U128:
        """Get real-time pending reward estimate for a token."""
        if not self._is_reward_token(reward_token):
            return U128(0)

        # Get latest reward per token
        now = self.env.ledger().timestamp()
        last_applicable = self._last_time_reward_applicable(reward_token, now)
        last_update = self.storage.get(f"last_update_time:{reward_token}")
        
        rpt = self.storage.get(f"reward_per_token_stored:{reward_token}")
        
        if last_applicable > last_update:
            total_staked = self.storage.get("total_staked")
            if total_staked > U128(0):
                rate = self.storage.get(f"reward_rate:{reward_token}")
                duration = U128(last_applicable - last_update)
                rpt += (duration * rate * PRECISION) / total_staked

        # Calculate user earnings
        user_stake = self.storage.get(f"user_stake:{user}", U128(0))
        paid = self.storage.get(f"user_reward_per_token_paid:{user}:{reward_token}", U128(0))
        accrued = self.storage.get(f"user_rewards:{user}:{reward_token}", U128(0))
        
        return accrued + (user_stake * (rpt - paid)) / PRECISION

    @view
    def get_reward_details(self, reward_token: Address) -> Map:
        """Get configuration and statistics for a reward token."""
        if not self._is_reward_token(reward_token):
            return {
                "active": False
            }
        return {
            "active": True,
            "rate": self.storage.get(f"reward_rate:{reward_token}"),
            "period_finish": self.storage.get(f"period_finish:{reward_token}"),
            "last_update_time": self.storage.get(f"last_update_time:{reward_token}"),
            "reward_per_token_stored": self.storage.get(f"reward_per_token_stored:{reward_token}"),
            "reward_pool": self.storage.get(f"reward_pool:{reward_token}"),
        }

    # ── Internal Helpers ─────────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _is_reward_token(self, token: Address) -> Bool:
        tokens = self.storage.get("reward_tokens")
        for i in range(tokens.len()):
            if tokens.get(i) == token:
                return True
        return False

    def _last_time_reward_applicable(self, token: Address, now: U64) -> U64:
        period_finish = self.storage.get(f"period_finish:{token}")
        return now if now < period_finish else period_finish

    def _update_reward(self, token: Address, user: Address or None):
        """
        Recalculate global reward index for token and snapshot for user.
        """
        now = self.env.ledger().timestamp()
        last_applicable = self._last_time_reward_applicable(token, now)
        last_update = self.storage.get(f"last_update_time:{token}")

        rpt = self.storage.get(f"reward_per_token_stored:{token}")

        if last_applicable > last_update:
            total_staked = self.storage.get("total_staked")
            if total_staked > U128(0):
                rate = self.storage.get(f"reward_rate:{token}")
                duration = U128(last_applicable - last_update)
                rpt += (duration * rate * PRECISION) / total_staked
            
            self.storage.set(f"reward_per_token_stored:{token}", rpt)
            self.storage.set(f"last_update_time:{token}", last_applicable)

        if user is not None:
            user_stake = self.storage.get(f"user_stake:{user}", U128(0))
            paid = self.storage.get(f"user_reward_per_token_paid:{user}:{token}", U128(0))
            accrued = self.storage.get(f"user_rewards:{user}:{token}", U128(0))
            
            new_accrued = accrued + (user_stake * (rpt - paid)) / PRECISION
            self.storage.set(f"user_rewards:{user}:{token}", new_accrued)
            self.storage.set(f"user_reward_per_token_paid:{user}:{token}", rpt)

    def _update_all_rewards(self, user: Address):
        """
        Update rewards across all registered tokens for user.
        """
        tokens = self.storage.get("reward_tokens")
        for i in range(tokens.len()):
            token = tokens.get(i)
            self._update_reward(token, user)
