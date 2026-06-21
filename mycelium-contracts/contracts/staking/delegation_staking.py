"""
DelegationStaking — Validator delegation contract with commission and slashing.

Mycelium Smart Contract for Stellar
Enables users to delegate staking tokens to validators.
Validators earn commission on delegator rewards.
Supports O(1) validator slashing, redelegation, and unbonding periods.
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
    VALIDATOR_NOT_FOUND = 5
    VALIDATOR_ALREADY_EXISTS = 6
    INVALID_COMMISSION = 7
    INSUFFICIENT_DELEGATION = 8
    DELEGATION_BELOW_MIN = 9
    LIMIT_EXCEEDED = 10
    NO_UNBONDED_TOKENS = 11
    INVALID_SLASH_BPS = 12
    NO_REWARDS = 13


# ── Constants ────────────────────────────────────────────────────────────────

BPS_BASE = U64(10000)                # 100% in basis points
PRECISION = U128(1_000_000_000_000)  # 1e12 for reward scaling


@contract
class DelegationStaking:
    """
    Staking contract designed for Proof-of-Stake delegation systems.
    Handles reward index tracking, commissions, slashing, unbonding, and redelegation.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ── Admin & Validator Configuration ──────────────────────────────────

    @external
    def initialize(
        self,
        admin: Address,
        staking_token: Address,
        reward_token: Address,
        reward_per_second: U128,
        unbonding_period: U64,
        min_delegation: U128,
    ):
        """
        One-time initialization of delegation contract parameters.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("staking_token", staking_token)
        self.storage.set("reward_token", reward_token)
        self.storage.set("reward_per_second", reward_per_second)
        self.storage.set("unbonding_period", unbonding_period)
        self.storage.set("min_delegation", min_delegation)
        
        self.storage.set("global_reward_index", U128(0))
        self.storage.set("global_last_update", self.env.ledger().timestamp())
        self.storage.set("total_global_stake", U128(0))
        self.storage.set("validators", Vec())
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "staking_token": staking_token,
            "reward_token": reward_token,
        })

    @external
    def register_validator(self, caller: Address, validator: Address, commission_bps: U64):
        """
        Register a new validator. Caller must be admin or validator key itself.
        """
        self._require_initialized()
        
        # Admin or the validator self-registers
        admin = self.storage.get("admin")
        if caller != admin and caller != validator:
            raise ContractError.UNAUTHORIZED
        caller.require_auth()

        if commission_bps > BPS_BASE:
            raise ContractError.INVALID_COMMISSION

        validators = self.storage.get("validators")
        for i in range(validators.len()):
            if validators.get(i) == validator:
                raise ContractError.VALIDATOR_ALREADY_EXISTS

        validators.append(validator)
        self.storage.set("validators", validators)

        self.storage.set(f"val_commission:{validator}", commission_bps)
        self.storage.set(f"val_stake:{validator}", U128(0))
        self.storage.set(f"val_multiplier:{validator}", BPS_BASE)
        self.storage.set(f"val_last_global_index:{validator}", self.storage.get("global_reward_index"))
        self.storage.set(f"val_reward_index:{validator}", U128(0))
        self.storage.set(f"val_owner_rewards:{validator}", U128(0))

        self.env.emit_event("validator_registered", {
            "validator": validator,
            "commission_bps": commission_bps,
        })

    @external
    def slash_validator(self, caller: Address, validator: Address, slash_bps: U64):
        """
        Admin-only: slash a validator's stake.
        Reduces validator multiplier, reducing all delegators' active stake.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        if not self._is_validator(validator):
            raise ContractError.VALIDATOR_NOT_FOUND

        if slash_bps == U64(0) or slash_bps > BPS_BASE:
            raise ContractError.INVALID_SLASH_BPS

        self._update_global_index()
        self._update_validator_state(validator)

        # Apply slash to multiplier
        mult = self.storage.get(f"val_multiplier:{validator}")
        slash_amount_bps = (mult * slash_bps) / BPS_BASE
        new_mult = mult - slash_amount_bps
        self.storage.set(f"val_multiplier:{validator}", new_mult)

        # Update stake totals
        old_stake = self.storage.get(f"val_stake:{validator}")
        slashed_stake = (old_stake * U128(slash_bps)) / U128(BPS_BASE)
        new_stake = old_stake - slashed_stake
        self.storage.set(f"val_stake:{validator}", new_stake)

        total_global = self.storage.get("total_global_stake")
        self.storage.set("total_global_stake", total_global - slashed_stake)

        self.env.emit_event("validator_slashed", {
            "validator": validator,
            "slashed_stake": slashed_stake,
            "new_multiplier": new_mult,
        })

    # ── User Delegation Actions ──────────────────────────────────────────

    @external
    def delegate(self, user: Address, validator: Address, amount: U128):
        """
        Delegate tokens to a validator.
        """
        user.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        if not self._is_validator(validator):
            raise ContractError.VALIDATOR_NOT_FOUND

        self._update_global_index()
        self._update_validator_state(validator)
        self._settle_delegator_rewards(user, validator)

        # Check limits
        min_delegation = self.storage.get("min_delegation")
        current_shares = self.storage.get(f"shares:{user}:{validator}", U128(0))
        if current_shares + amount < min_delegation:
            raise ContractError.DELEGATION_BELOW_MIN

        # Transfer tokens to contract
        staking_token = self.storage.get("staking_token")
        self.env.transfer(user, self.env.current_contract(), staking_token, amount)

        # Update states
        self.storage.set(f"shares:{user}:{validator}", current_shares + amount)
        
        val_stake = self.storage.get(f"val_stake:{validator}")
        self.storage.set(f"val_stake:{validator}", val_stake + amount)

        global_stake = self.storage.get("total_global_stake")
        self.storage.set("total_global_stake", global_stake + amount)

        self.env.emit_event("delegated", {
            "user": user,
            "validator": validator,
            "amount": amount,
        })

    @external
    def undelegate(self, user: Address, validator: Address, amount: U128):
        """
        Undelegate tokens from a validator. Begins unbonding cooldown.
        """
        user.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        current_shares = self.storage.get(f"shares:{user}:{validator}", U128(0))
        if current_shares < amount:
            raise ContractError.INSUFFICIENT_DELEGATION

        self._update_global_index()
        self._update_validator_state(validator)
        self._settle_delegator_rewards(user, validator)

        # Calculate actual slashed amount corresponding to the share withdrawal
        mult = self.storage.get(f"val_multiplier:{validator}")
        withdrawable = (amount * U128(mult)) / U128(BPS_BASE)

        # Update states
        self.storage.set(f"shares:{user}:{validator}", current_shares - amount)
        
        val_stake = self.storage.get(f"val_stake:{validator}")
        # Make sure we don't underflow
        new_val_stake = val_stake - withdrawable if val_stake > withdrawable else U128(0)
        self.storage.set(f"val_stake:{validator}", new_val_stake)

        global_stake = self.storage.get("total_global_stake")
        new_global_stake = global_stake - withdrawable if global_stake > withdrawable else U128(0)
        self.storage.set("total_global_stake", new_global_stake)

        # Create unbonding entry
        now = self.env.ledger().timestamp()
        period = self.storage.get("unbonding_period")
        
        unbonds = self.storage.get(f"unbonds:{user}", Vec())
        unbonds.append({
            "amount": withdrawable,
            "unlock_time": now + period,
        })
        self.storage.set(f"unbonds:{user}", unbonds)

        self.env.emit_event("undelegated", {
            "user": user,
            "validator": validator,
            "amount_shares": amount,
            "withdrawable_stake": withdrawable,
            "unlock_time": now + period,
        })

    @external
    def redelegate(self, user: Address, from_validator: Address, to_validator: Address, amount: U128):
        """
        Move delegation directly from one validator to another without unbonding delay.
        Takes into account the slashing multiplier of the source validator.
        """
        user.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        if not self._is_validator(to_validator):
            raise ContractError.VALIDATOR_NOT_FOUND

        from_shares = self.storage.get(f"shares:{user}:{from_validator}", U128(0))
        if from_shares < amount:
            raise ContractError.INSUFFICIENT_DELEGATION

        self._update_global_index()
        self._update_validator_state(from_validator)
        self._update_validator_state(to_validator)
        self._settle_delegator_rewards(user, from_validator)
        self._settle_delegator_rewards(user, to_validator)

        # Deduct from source
        from_mult = self.storage.get(f"val_multiplier:{from_validator}")
        withdrawable = (amount * U128(from_mult)) / U128(BPS_BASE)

        self.storage.set(f"shares:{user}:{from_validator}", from_shares - amount)
        from_val_stake = self.storage.get(f"val_stake:{from_validator}")
        self.storage.set(f"val_stake:{from_validator}", from_val_stake - withdrawable if from_val_stake > withdrawable else U128(0))

        # Add to destination (delegates the withdrawable amount)
        to_shares = self.storage.get(f"shares:{user}:{to_validator}", U128(0))
        self.storage.set(f"shares:{user}:{to_validator}", to_shares + withdrawable)
        to_val_stake = self.storage.get(f"val_stake:{to_validator}")
        self.storage.set(f"val_stake:{to_validator}", to_val_stake + withdrawable)

        self.env.emit_event("redelegated", {
            "user": user,
            "from_validator": from_validator,
            "to_validator": to_validator,
            "withdrawn_shares": amount,
            "delegated_amount": withdrawable,
        })

    @external
    def withdraw_unbonded(self, user: Address):
        """
        Withdraw all fully unbonded tokens.
        """
        user.require_auth()
        self._require_initialized()

        unbonds = self.storage.get(f"unbonds:{user}", Vec())
        if unbonds.len() == 0:
            raise ContractError.NO_UNBONDED_TOKENS

        now = self.env.ledger().timestamp()
        total_payout = U128(0)
        remaining_unbonds = Vec()

        for i in range(unbonds.len()):
            entry = unbonds.get(i)
            if now >= entry["unlock_time"]:
                total_payout += entry["amount"]
            else:
                remaining_unbonds.append(entry)

        if total_payout == U128(0):
            raise ContractError.NO_UNBONDED_TOKENS

        self.storage.set(f"unbonds:{user}", remaining_unbonds)

        staking_token = self.storage.get("staking_token")
        self.env.transfer(self.env.current_contract(), user, staking_token, total_payout)

        self.env.emit_event("unbonded_withdrawn", {
            "user": user,
            "amount": total_payout,
        })

    @external
    def claim_rewards(self, user: Address, validator: Address):
        """
        Claim pending delegation rewards from a validator.
        """
        user.require_auth()
        self._require_initialized()

        self._update_global_index()
        self._update_validator_state(validator)
        self._settle_delegator_rewards(user, validator)

        rewards = self.storage.get(f"reward_accum:{user}:{validator}", U128(0))
        if rewards == U128(0):
            raise ContractError.NO_REWARDS

        self.storage.set(f"reward_accum:{user}:{validator}", U128(0))

        reward_token = self.storage.get("reward_token")
        self.env.transfer(self.env.current_contract(), user, reward_token, rewards)

        self.env.emit_event("rewards_claimed", {
            "user": user,
            "validator": validator,
            "amount": rewards,
        })

    @external
    def claim_validator_commission(self, validator: Address):
        """
        Claim accumulated commission rewards for the validator owner.
        """
        validator.require_auth()
        self._require_initialized()

        self._update_global_index()
        self._update_validator_state(validator)

        commission_rewards = self.storage.get(f"val_owner_rewards:{validator}", U128(0))
        if commission_rewards == U128(0):
            raise ContractError.NO_REWARDS

        self.storage.set(f"val_owner_rewards:{validator}", U128(0))

        reward_token = self.storage.get("reward_token")
        self.env.transfer(self.env.current_contract(), validator, reward_token, commission_rewards)

        self.env.emit_event("commission_claimed", {
            "validator": validator,
            "amount": commission_rewards,
        })

    # ── View Functions ───────────────────────────────────────────────────

    @view
    def get_delegation(self, user: Address, validator: Address) -> Map:
        """
        Get info about a user's delegation to a validator.
        """
        mult = self.storage.get(f"val_multiplier:{validator}", BPS_BASE)
        shares = self.storage.get(f"shares:{user}:{validator}", U128(0))
        effective_stake = (shares * U128(mult)) / U128(BPS_BASE)
        
        return {
            "shares": shares,
            "effective_stake": effective_stake,
        }

    @view
    def get_unbonds(self, user: Address) -> Vec:
        """
        Get list of user's active unbonding periods.
        """
        return self.storage.get(f"unbonds:{user}", Vec())

    @view
    def get_validators(self) -> Vec:
        """
        Get list of registered validator addresses.
        """
        return self.storage.get("validators")

    @view
    def get_validator_info(self, validator: Address) -> Map:
        """
        Get configurations and current stake for a validator.
        """
        return {
            "commission_bps": self.storage.get(f"val_commission:{validator}", U64(0)),
            "stake": self.storage.get(f"val_stake:{validator}", U128(0)),
            "multiplier": self.storage.get(f"val_multiplier:{validator}", BPS_BASE),
            "owner_rewards": self.storage.get(f"val_owner_rewards:{validator}", U128(0)),
        }

    # ── Internal Helpers ─────────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _is_validator(self, validator: Address) -> Bool:
        vals = self.storage.get("validators")
        for i in range(vals.len()):
            if vals.get(i) == validator:
                return True
        return False

    def _update_global_index(self):
        """
        Increment the global reward index.
        """
        now = self.env.ledger().timestamp()
        last = self.storage.get("global_last_update")
        if now <= last:
            return

        total_stake = self.storage.get("total_global_stake")
        if total_stake > U128(0):
            elapsed = U128(now - last)
            rate = self.storage.get("reward_per_second")
            global_index = self.storage.get("global_reward_index")
            global_index += (elapsed * rate * PRECISION) / total_stake
            self.storage.set("global_reward_index", global_index)

        self.storage.set("global_last_update", now)

    def _update_validator_state(self, validator: Address):
        """
        Sync validator-specific indices up to the current global index.
        """
        global_index = self.storage.get("global_reward_index")
        val_last_global = self.storage.get(f"val_last_global_index:{validator}")

        if global_index > val_last_global:
            val_stake = self.storage.get(f"val_stake:{validator}")
            if val_stake > U128(0):
                commission_bps = self.storage.get(f"val_commission:{validator}")
                delta = global_index - val_last_global

                # Calculate validator owner commission share
                val_commission_rewards = (val_stake * delta * U128(commission_bps)) / (U128(BPS_BASE) * PRECISION)
                old_owner_rewards = self.storage.get(f"val_owner_rewards:{validator}", U128(0))
                self.storage.set(f"val_owner_rewards:{validator}", old_owner_rewards + val_commission_rewards)

                # Rest goes to delegators
                val_reward_index = self.storage.get(f"val_reward_index:{validator}")
                delegator_share_index = (delta * U128(BPS_BASE - commission_bps)) / U128(BPS_BASE)
                self.storage.set(f"val_reward_index:{validator}", val_reward_index + delegator_share_index)

            self.storage.set(f"val_last_global_index:{validator}", global_index)

    def _settle_delegator_rewards(self, user: Address, validator: Address):
        """
        Settle pending rewards for a delegator using validator reward index.
        """
        val_index = self.storage.get(f"val_reward_index:{validator}")
        user_index = self.storage.get(f"user_reward_index:{user}:{validator}", U128(0))

        if val_index > user_index:
            shares = self.storage.get(f"shares:{user}:{validator}", U128(0))
            if shares > U128(0):
                mult = self.storage.get(f"val_multiplier:{validator}")
                effective_shares = (shares * U128(mult)) / U128(BPS_BASE)

                delta = val_index - user_index
                accrued = (effective_shares * delta) / PRECISION
                
                old_accum = self.storage.get(f"reward_accum:{user}:{validator}", U128(0))
                self.storage.set(f"reward_accum:{user}:{validator}", old_accum + accrued)

        self.storage.set(f"user_reward_index:{user}:{validator}", val_index)
