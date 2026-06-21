"""
Time Locked Vault — Assets timelocks with decaying early exit penalties and reward redistribution.

Mycelium Smart Contract for Stellar
Saves user deposits under customizable lockup periods. Early withdrawals trigger a penalty
that decays linearly over time. Penalties are accumulated and distributed to mature/honest stakers
using a reward-per-share distribution system. Emergency controls allow bypassing lockups.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    INVALID_PARAMETERS = 4
    LOCKUP_NOT_FOUND = 5
    LOCKUP_INACTIVE = 6
    EMERGENCY_ACTIVE = 7
    INSUFFICIENT_FUNDS = 8


# Lockup status states
STATUS_ACTIVE = 1
STATUS_WITHDRAWN = 2
STATUS_EARLY_WITHDRAWN = 3

SCALE_FACTOR = U128(1_000_000_000_000)  # 1e12 for reward share division accuracy


@contract
class TimeLockedVault:
    """
    Timelocked asset staking platform featuring custom lockups, early withdraw penalties,
    and a reward distribution model funded by early-exit fees.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        asset: Address,
        emergency_admin: Address,
        max_penalty_bps: U64,  # e.g., 5000 bps = 50% max penalty at start of lock
    ):
        """Initialize the Time Locked Vault contract."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if max_penalty_bps > 8000:  # Cap maximum penalty at 80%
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("admin", admin)
        self.storage.set("asset", asset)
        self.storage.set("emergency_admin", emergency_admin)
        self.storage.set("max_penalty_bps", max_penalty_bps)
        self.storage.set("emergency_active", False)

        # Reward pool trackers
        self.storage.set("total_shares", U128(0))
        self.storage.set("acc_reward_per_share", U128(0))
        self.storage.set("lockup_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "asset": asset,
            "max_penalty_bps": max_penalty_bps,
        })

    @external
    def deposit(self, sender: Address, amount: U128, duration: U64) -> U64:
        """Deposit assets into a timelocked vault for a given duration (in seconds)."""
        sender.require_auth()
        self._require_initialized()

        # Constraints on lock duration (e.g. minimum 7 days, maximum 5 years)
        if duration < 604800 or duration > 157680000:
            raise ContractError.INVALID_PARAMETERS

        if amount == 0:
            raise ContractError.INVALID_PARAMETERS

        # 1. Harvest any pending reward for this user before altering shares
        self._claim_pending_rewards(sender)

        # 2. Transfer asset to the contract
        asset = self.storage.get("asset")
        self.env.transfer(asset, sender, self.env.current_contract(), amount)

        # 3. Calculate shares based on lock duration multiplier
        # 7-day lock: 1.0x (100)
        # 30-day lock: 1.2x (120)
        # 1-year lock: 2.0x (200)
        # 5-year lock: 4.0x (400)
        # Formula: multiplier = 100 + (duration - 604800) * 300 // (157680000 - 604800)
        multiplier = U128(100) + (U128(duration - 604800) * U128(300)) // U128(157075200)
        shares = (amount * multiplier) // U128(100)

        # 4. Create lockup entry
        lockup_id = self.storage.get("lockup_count", U64(0)) + U64(1)
        self.storage.set("lockup_count", lockup_id)

        current_time = self.env.ledger().timestamp()

        self.storage.set(f"lockup:{lockup_id}:owner", sender)
        self.storage.set(f"lockup:{lockup_id}:amount", amount)
        self.storage.set(f"lockup:{lockup_id}:shares", shares)
        self.storage.set(f"lockup:{lockup_id}:lock_time", current_time)
        self.storage.set(f"lockup:{lockup_id}:unlock_time", current_time + duration)
        self.storage.set(f"lockup:{lockup_id}:duration", duration)
        self.storage.set(f"lockup:{lockup_id}:status", U64(STATUS_ACTIVE))

        # Update global share states
        total_shares = self.storage.get("total_shares", U128(0))
        self.storage.set("total_shares", total_shares + shares)

        # Increase user shares
        user_shares = self.storage.get(f"user_shares:{sender}", U128(0))
        self.storage.set(f"user_shares:{sender}", user_shares + shares)

        # Update reward debt
        acc_reward_per_share = self.storage.get("acc_reward_per_share", U128(0))
        self.storage.set(f"reward_debt:{sender}", (user_shares + shares) * acc_reward_per_share // SCALE_FACTOR)

        self.env.emit_event("deposited", {
            "lockup_id": lockup_id,
            "owner": sender,
            "amount": amount,
            "shares": shares,
            "unlock_time": current_time + duration,
        })

        return lockup_id

    @external
    def withdraw(self, sender: Address, lockup_id: U64):
        """Withdraw locked assets. Performs early withdrawal penalty calculations if timelock is active."""
        sender.require_auth()
        self._require_initialized()

        self._check_lockup_exists(lockup_id)

        owner = self.storage.get(f"lockup:{lockup_id}:owner")
        if sender != owner:
            raise ContractError.UNAUTHORIZED

        status = self.storage.get(f"lockup:{lockup_id}:status")
        if status != STATUS_ACTIVE:
            raise ContractError.LOCKUP_INACTIVE

        # Claim current pending rewards
        self._claim_pending_rewards(sender)

        amount = self.storage.get(f"lockup:{lockup_id}:amount")
        shares = self.storage.get(f"lockup:{lockup_id}:shares")
        unlock_time = self.storage.get(f"lockup:{lockup_id}:unlock_time")
        duration = self.storage.get(f"lockup:{lockup_id}:duration")
        lock_time = self.storage.get(f"lockup:{lockup_id}:lock_time")

        current_time = self.env.ledger().timestamp()
        emergency_active = self.storage.get("emergency_active", False)

        penalty_amount = U128(0)
        withdrawn_status = STATUS_WITHDRAWN

        # If timelock is not mature and emergency mode is not active, apply penalty
        if (current_time < unlock_time) and (not emergency_active):
            withdrawn_status = STATUS_EARLY_WITHDRAWN
            
            # Linear penalty decay: penalty_bps = max_penalty_bps * time_remaining / total_duration
            time_left = unlock_time - current_time
            max_penalty = self.storage.get("max_penalty_bps")
            
            penalty_bps = (U128(max_penalty) * U128(time_left)) // U128(duration)
            penalty_amount = (amount * penalty_bps) // U128(10000)

        payout = amount - penalty_amount

        # 1. Update status
        self.storage.set(f"lockup:{lockup_id}:status", U64(withdrawn_status))

        # 2. Subtract user shares
        user_shares = self.storage.get(f"user_shares:{sender}", U128(0))
        self.storage.set(f"user_shares:{sender}", user_shares - shares)

        total_shares = self.storage.get("total_shares")
        self.storage.set("total_shares", total_shares - shares)

        # Update reward debt
        acc_reward_per_share = self.storage.get("acc_reward_per_share", U128(0))
        self.storage.set(f"reward_debt:{sender}", (user_shares - shares) * acc_reward_per_share // SCALE_FACTOR)

        # 3. Distribute penalty to reward pool if penalty occurred
        asset = self.storage.get("asset")
        if penalty_amount > 0:
            # We split the penalty: 20% to treasury (admin), 80% redistributed to remaining stakers
            treasury_cut = (penalty_amount * U128(2000)) // U128(10000)
            redistribute_cut = penalty_amount - treasury_cut

            if treasury_cut > 0:
                admin = self.storage.get("admin")
                self.env.transfer(asset, self.env.current_contract(), admin, treasury_cut)

            # Redistribution logic
            remaining_shares = total_shares - shares
            if remaining_shares > 0 and redistribute_cut > 0:
                # Add reward to stakers
                acc_reward_per_share = acc_reward_per_share + (redistribute_cut * SCALE_FACTOR) // remaining_shares
                self.storage.set("acc_reward_per_share", acc_reward_per_share)
            else:
                # No active stakers left: refund remainder to admin
                admin = self.storage.get("admin")
                self.env.transfer(asset, self.env.current_contract(), admin, redistribute_cut)

        # 4. Transfer payout to user
        if payout > 0:
            self.env.transfer(asset, self.env.current_contract(), sender, payout)

        self.env.emit_event("withdrawn", {
            "lockup_id": lockup_id,
            "owner": sender,
            "payout": payout,
            "penalty": penalty_amount,
            "status": withdrawn_status,
        })

    @external
    def claim_rewards(self, sender: Address):
        """Allows users to manually harvest accumulated rewards from penalty redistribution."""
        sender.require_auth()
        self._require_initialized()

        self._claim_pending_rewards(sender)

    @external
    def toggle_emergency_exit(self, emergency_admin: Address):
        """Toggle emergency exit mode. Disables all locks and early withdraw penalties."""
        emergency_admin.require_auth()
        self._require_initialized()

        active_admin = self.storage.get("emergency_admin")
        if emergency_admin != active_admin:
            raise ContractError.UNAUTHORIZED

        current_val = self.storage.get("emergency_active", False)
        self.storage.set("emergency_active", not current_val)

        self.env.emit_event("emergency_mode_updated", {"active": not current_val})

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_lockup(self, lockup_id: U64) -> Map:
        """Query lockup data."""
        self._check_lockup_exists(lockup_id)
        return {
            "owner": self.storage.get(f"lockup:{lockup_id}:owner"),
            "amount": self.storage.get(f"lockup:{lockup_id}:amount"),
            "shares": self.storage.get(f"lockup:{lockup_id}:shares"),
            "lock_time": self.storage.get(f"lockup:{lockup_id}:lock_time"),
            "unlock_time": self.storage.get(f"lockup:{lockup_id}:unlock_time"),
            "duration": self.storage.get(f"lockup:{lockup_id}:duration"),
            "status": self.storage.get(f"lockup:{lockup_id}:status"),
        }

    @view
    def get_pending_rewards(self, user: Address) -> U128:
        """Calculate outstanding claimable rewards for a user."""
        user_shares = self.storage.get(f"user_shares:{user}", U128(0))
        if user_shares == 0:
            return self.storage.get(f"unclaimed_rewards:{user}", U128(0))

        acc_reward_per_share = self.storage.get("acc_reward_per_share", U128(0))
        reward_debt = self.storage.get(f"reward_debt:{user}", U128(0))
        
        accrued = (user_shares * acc_reward_per_share) // SCALE_FACTOR
        pending = accrued - reward_debt
        
        unclaimed = self.storage.get(f"unclaimed_rewards:{user}", U128(0))
        return unclaimed + pending

    @view
    def get_pool_details(self) -> Map:
        """Retrieve total shares, lockup count, and emergency status."""
        return {
            "total_shares": self.storage.get("total_shares"),
            "lockup_count": self.storage.get("lockup_count"),
            "emergency_active": self.storage.get("emergency_active"),
        }

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _check_lockup_exists(self, lockup_id: U64):
        total = self.storage.get("lockup_count", U64(0))
        if lockup_id == 0 or lockup_id > total:
            raise ContractError.LOCKUP_NOT_FOUND

    def _claim_pending_rewards(self, user: Address):
        pending = self.get_pending_rewards(user)
        user_shares = self.storage.get(f"user_shares:{user}", U128(0))

        # Reset pending and update debt
        acc_reward_per_share = self.storage.get("acc_reward_per_share", U128(0))
        self.storage.set(f"reward_debt:{user}", user_shares * acc_reward_per_share // SCALE_FACTOR)
        self.storage.set(f"unclaimed_rewards:{user}", U128(0))

        if pending > 0:
            asset = self.storage.get("asset")
            # Transfer rewards from contract pool
            self.env.transfer(asset, self.env.current_contract(), user, pending)
            self.env.emit_event("rewards_claimed", {
                "user": user,
                "amount": pending,
            })
