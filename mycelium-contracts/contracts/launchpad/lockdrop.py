"""
Lockdrop — Lock native tokens for durations, earn launch token allocation, lock duration multipliers, early unlock penalties.

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
    PHASE_NOT_ACTIVE = 4
    INVALID_TIER = 5
    ZERO_AMOUNT = 6
    DEPOSIT_NOT_FOUND = 7
    NOT_UNLOCKED = 8
    ALREADY_WITHDRAWN = 9
    ALREADY_CLAIMED = 10
    CLAIM_NOT_ACTIVE = 11
    INSUFFICIENT_POOL = 12
    INVALID_TIME_RANGE = 13
    INVALID_PENALTY = 14

@contract
class Lockdrop:
    """A token lockdrop contract distributing launch tokens based on lock duration and amount."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        lock_token: Address,
        launch_token: Address,
        treasury: Address,
        lock_start: U64,
        lock_end: U64,
        claim_start: U64,
        early_unlock_penalty_bps: U32,  # e.g., 2000 for 20%
    ):
        """Initialize the lockdrop contract.

        Args:
            admin: Admin address.
            lock_token: Address of token to be locked.
            launch_token: Address of token to be distributed.
            treasury: Address receiving early unlock penalties.
            lock_start: Timestamp when deposits start.
            lock_end: Timestamp when deposits end.
            claim_start: Timestamp when rewards can be claimed.
            early_unlock_penalty_bps: Penalty in basis points for early unlock.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if lock_start >= lock_end or lock_end > claim_start:
            raise ContractError.INVALID_TIME_RANGE

        if early_unlock_penalty_bps > 10000:
            raise ContractError.INVALID_PENALTY

        self.storage.set("admin", admin)
        self.storage.set("lock_token", lock_token)
        self.storage.set("launch_token", launch_token)
        self.storage.set("treasury", treasury)
        self.storage.set("lock_start", lock_start)
        self.storage.set("lock_end", lock_end)
        self.storage.set("claim_start", claim_start)
        self.storage.set("early_unlock_penalty_bps", early_unlock_penalty_bps)

        self.storage.set("total_score", U128(0))
        self.storage.set("total_launch_rewards", U128(0))
        self.storage.set("next_deposit_id", U64(0))
        self.storage.set("initialized", True)

        # Set default duration multipliers:
        # Tier 0: 30 days (2592000s) -> 1.0x (10000 bps)
        # Tier 1: 90 days (7776000s) -> 1.5x (15000 bps)
        # Tier 2: 180 days (15552000s) -> 2.0x (20000 bps)
        self._set_tier_config(0, U64(2592000), 10000)
        self._set_tier_config(1, U64(7776000), 15000)
        self._set_tier_config(2, U64(15552000), 20000)

        self.env.emit_event("initialized", {
            "lock_token": lock_token,
            "launch_token": launch_token,
            "lock_start": lock_start,
            "lock_end": lock_end,
        })

    @external
    def fund_reward_pool(self, admin: Address, amount: U128):
        """Fund the reward pool with launch tokens.

        Args:
            admin: Admin address.
            amount: Launch token amount.
        """
        self._require_initialized()
        admin.require_auth()
        self._require_admin(admin)

        if amount == 0:
            raise ContractError.ZERO_AMOUNT

        launch_token = self.storage.get("launch_token")
        self.env.invoke_contract(
            launch_token,
            "transfer",
            [admin, self.env.current_contract_address(), amount]
        )

        total_rewards = self.storage.get("total_launch_rewards")
        self.storage.set("total_launch_rewards", total_rewards + amount)

        self.env.emit_event("pool_funded", {"amount": amount})

    @external
    def lock_tokens(
        self,
        caller: Address,
        amount: U128,
        tier_id: U32,
    ) -> U64:
        """Lock tokens in a specific duration tier to earn launch token allocation.

        Args:
            caller: Account locking the tokens.
            amount: Amount of tokens to lock.
            tier_id: ID of the lock duration tier (0, 1, or 2).
        """
        self._require_initialized()
        caller.require_auth()

        now = self.env.ledger().timestamp()
        lock_start = self.storage.get("lock_start")
        lock_end = self.storage.get("lock_end")

        if now < lock_start or now >= lock_end:
            raise ContractError.PHASE_NOT_ACTIVE

        if amount == 0:
            raise ContractError.ZERO_AMOUNT

        # Retrieve tier configuration
        if not self.storage.get(("tier_exists", tier_id), False):
            raise ContractError.INVALID_TIER

        duration = self.storage.get(("tier_duration", tier_id))
        multiplier = self.storage.get(("tier_multiplier", tier_id))

        # Calculate score: amount * multiplier / 10000
        score = (amount * U128(multiplier)) / U128(10000)

        # Transfer lock tokens from user to contract
        lock_token = self.storage.get("lock_token")
        self.env.invoke_contract(
            lock_token,
            "transfer",
            [caller, self.env.current_contract_address(), amount]
        )

        # Record deposit
        deposit_id = self.storage.get("next_deposit_id")
        self.storage.set("next_deposit_id", deposit_id + U64(1))

        self.storage.set(("dep_user", deposit_id), caller)
        self.storage.set(("dep_amount", deposit_id), amount)
        self.storage.set(("dep_start", deposit_id), now)
        self.storage.set(("dep_unlock", deposit_id), now + duration)
        self.storage.set(("dep_score", deposit_id), score)
        self.storage.set(("dep_claimed", deposit_id), False)
        self.storage.set(("dep_withdrawn", deposit_id), False)

        # Update total scores
        total_score = self.storage.get("total_score")
        self.storage.set("total_score", total_score + score)

        # Keep track of user's active deposit count & deposit IDs
        user_dep_count = self.storage.get(("user_dep_count", caller), U32(0))
        self.storage.set(("user_dep", caller, user_dep_count), deposit_id)
        self.storage.set(("user_dep_count", caller), user_dep_count + U32(1))

        self.env.emit_event("tokens_locked", {
            "deposit_id": deposit_id,
            "user": caller,
            "amount": amount,
            "unlock_time": now + duration,
            "score": score,
        })

        return deposit_id

    @external
    def withdraw(self, caller: Address, deposit_id: U64):
        """Withdraw locked tokens after the lock period has expired.

        Args:
            caller: Owner of the deposit.
            deposit_id: ID of the deposit to withdraw.
        """
        self._require_initialized()
        caller.require_auth()

        self._validate_deposit_owner(caller, deposit_id)

        if self.storage.get(("dep_withdrawn", deposit_id), False):
            raise ContractError.ALREADY_WITHDRAWN

        now = self.env.ledger().timestamp()
        unlock_time = self.storage.get(("dep_unlock", deposit_id))
        if now < unlock_time:
            raise ContractError.NOT_UNLOCKED

        self.storage.set(("dep_withdrawn", deposit_id), True)
        amount = self.storage.get(("dep_amount", deposit_id))

        lock_token = self.storage.get("lock_token")
        self.env.invoke_contract(
            lock_token,
            "transfer",
            [self.env.current_contract_address(), caller, amount]
        )

        self.env.emit_event("tokens_withdrawn", {
            "deposit_id": deposit_id,
            "user": caller,
            "amount": amount,
            "penalty_paid": U128(0),
        })

    @external
    def early_withdraw(self, caller: Address, deposit_id: U64):
        """Withdraw locked tokens early by paying a penalty fee. Deducts allocation score if not claimed.

        Args:
            caller: Owner of the deposit.
            deposit_id: ID of the deposit to withdraw.
        """
        self._require_initialized()
        caller.require_auth()

        self._validate_deposit_owner(caller, deposit_id)

        if self.storage.get(("dep_withdrawn", deposit_id), False):
            raise ContractError.ALREADY_WITHDRAWN

        now = self.env.ledger().timestamp()
        unlock_time = self.storage.get(("dep_unlock", deposit_id))
        if now >= unlock_time:
            # Should use normal withdraw method
            self.withdraw(caller, deposit_id)
            return

        self.storage.set(("dep_withdrawn", deposit_id), True)

        amount = self.storage.get(("dep_amount", deposit_id))
        penalty_bps = self.storage.get("early_unlock_penalty_bps")
        
        penalty_amount = (amount * U128(penalty_bps)) / U128(10000)
        net_returned = amount - penalty_amount

        # If they haven't claimed reward yet, forfeit the allocation score
        claimed = self.storage.get(("dep_claimed", deposit_id))
        if not claimed:
            score = self.storage.get(("dep_score", deposit_id))
            total_score = self.storage.get("total_score")
            if total_score >= score:
                self.storage.set("total_score", total_score - score)
            # Set score to 0 to prevent claiming later
            self.storage.set(("dep_score", deposit_id), U128(0))

        # Transfer net returned lock tokens to user
        lock_token = self.storage.get("lock_token")
        self.env.invoke_contract(
            lock_token,
            "transfer",
            [self.env.current_contract_address(), caller, net_returned]
        )

        # Transfer penalty to treasury
        if penalty_amount > 0:
            treasury = self.storage.get("treasury")
            self.env.invoke_contract(
                lock_token,
                "transfer",
                [self.env.current_contract_address(), treasury, penalty_amount]
            )

        self.env.emit_event("tokens_withdrawn", {
            "deposit_id": deposit_id,
            "user": caller,
            "amount": net_returned,
            "penalty_paid": penalty_amount,
        })

    @external
    def claim_rewards(self, caller: Address, deposit_id: U64) -> U128:
        """Claim launch token rewards earned by locking.

        Args:
            caller: Owner of the deposit.
            deposit_id: ID of the deposit.
        """
        self._require_initialized()
        caller.require_auth()

        self._validate_deposit_owner(caller, deposit_id)

        now = self.env.ledger().timestamp()
        claim_start = self.storage.get("claim_start")
        if now < claim_start:
            raise ContractError.CLAIM_NOT_ACTIVE

        if self.storage.get(("dep_claimed", deposit_id), False):
            raise ContractError.ALREADY_CLAIMED

        score = self.storage.get(("dep_score", deposit_id))
        if score == 0:
            # Score has been forfeited due to early withdrawal
            raise ContractError.ZERO_AMOUNT

        total_score = self.storage.get("total_score")
        if total_score == 0:
            raise ContractError.ZERO_AMOUNT

        total_rewards = self.storage.get("total_launch_rewards")
        
        # reward = score * total_rewards / total_score
        reward_amount = (score * total_rewards) / total_score

        if reward_amount == 0:
            raise ContractError.ZERO_AMOUNT

        self.storage.set(("dep_claimed", deposit_id), True)

        launch_token = self.storage.get("launch_token")
        self.env.invoke_contract(
            launch_token,
            "transfer",
            [self.env.current_contract_address(), caller, reward_amount]
        )

        self.env.emit_event("rewards_claimed", {
            "deposit_id": deposit_id,
            "user": caller,
            "reward_amount": reward_amount,
        })

        return reward_amount

    @external
    def update_tier(self, admin: Address, tier_id: U32, duration: U64, multiplier: U32):
        """Update or create a lock tier duration and multiplier (Admin only).

        Args:
            admin: Administrative address.
            tier_id: The tier configuration ID.
            duration: Unlock duration in seconds.
            multiplier: Lock multiplier in basis points.
        """
        self._require_initialized()
        admin.require_auth()
        self._require_admin(admin)

        self._set_tier_config(tier_id, duration, multiplier)

    @view
    def get_deposit(self, deposit_id: U64) -> Map:
        """Fetch details of a specific deposit."""
        res = Map()
        if not self.storage.get(("dep_user", deposit_id)):
            raise ContractError.DEPOSIT_NOT_FOUND
        
        res.set("user", self.storage.get(("dep_user", deposit_id)))
        res.set("amount", self.storage.get(("dep_amount", deposit_id)))
        res.set("start_time", self.storage.get(("dep_start", deposit_id)))
        res.set("unlock_time", self.storage.get(("dep_unlock", deposit_id)))
        res.set("score", self.storage.get(("dep_score", deposit_id)))
        res.set("claimed", self.storage.get(("dep_claimed", deposit_id)))
        res.set("withdrawn", self.storage.get(("dep_withdrawn", deposit_id)))
        return res

    @view
    def get_user_deposits(self, user: Address) -> Vec:
        """Fetch list of deposit IDs for a specific user."""
        res = Vec()
        count = self.storage.get(("user_dep_count", user), U32(0))
        for i in range(count):
            dep_id = self.storage.get(("user_dep", user, i))
            res.append(dep_id)
        return res

    @view
    def get_info(self) -> Map:
        """Retrieve lockdrop status and parameters."""
        res = Map()
        res.set("admin", self.storage.get("admin"))
        res.set("lock_token", self.storage.get("lock_token"))
        res.set("launch_token", self.storage.get("launch_token"))
        res.set("lock_start", self.storage.get("lock_start"))
        res.set("lock_end", self.storage.get("lock_end"))
        res.set("claim_start", self.storage.get("claim_start"))
        res.set("total_score", self.storage.get("total_score"))
        res.set("total_launch_rewards", self.storage.get("total_launch_rewards"))
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

    def _validate_deposit_owner(self, caller: Address, deposit_id: U64):
        owner = self.storage.get(("dep_user", deposit_id))
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
