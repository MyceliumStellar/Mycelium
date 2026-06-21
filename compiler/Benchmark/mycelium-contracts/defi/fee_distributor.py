"""
Fee Distributor — Protocol fee collector and distributor.

Features:
  - Staking of governance/protocol tokens (e.g. MYC) to earn yield share
  - Historical checkpoint lists for users and global totals (time-travel lookup)
  - Epoch-based checkpointing (e.g. weekly epochs)
  - Accumulation of protocol fees in a primary distribution token (e.g. USDC)
  - Bulk claim rewards across multiple past finalized epochs
  - Fee conversion: allows admin/keepers to swap other collected tokens
    into the distribution token via pool integrations before epoch finalization
  - Security guards and reentrancy protection

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
    REENTRANCY_GUARD = 4
    ZERO_AMOUNT = 5
    INSUFFICIENT_STAKE = 6
    EPOCH_NOT_ELAPSED = 7
    INVALID_EPOCH_RANGE = 8
    ALREADY_CLAIMED = 9
    SWAP_FAILED = 10
    POOL_VALIDATION_FAILED = 11


# Constants
EPOCH_DURATION = U64(604800)  # 1 week in seconds


@contract
class FeeDistributor:
    """
    Distributes accumulated protocol fees proportionally to stakers using
    epoch checkpoints and historical balance tracking.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ------------------------------------------------------------------ #
    #  Initialization
    # ------------------------------------------------------------------ #

    @external
    def initialize(
        self,
        admin: Address,
        staking_token: Address,
        distribution_token: Address,
    ):
        """Initialise distributor parameters."""
        admin.require_auth()

        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        now = self.env.ledger().timestamp()

        self.storage.set("admin", admin)
        self.storage.set("staking_token", staking_token)
        self.storage.set("distribution_token", distribution_token)
        
        self.storage.set("current_epoch", U64(1))
        self.storage.set("last_epoch_checkpoint_time", now)
        self.storage.set("accumulated_fees", U128(0))
        
        # Staking tracking
        self.storage.set("total_staked", U128(0))
        self.storage.set("total_checkpoints", Vec())

        self.storage.set("reentrancy_locked", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "staking_token": staking_token,
            "distribution_token": distribution_token,
            "start_time": now
        })

    # ------------------------------------------------------------------ #
    #  Staking Custody Functions
    # ------------------------------------------------------------------ #

    @external
    def stake(self, user: Address, amount: U128):
        """Stake governance tokens to participate in fee distribution."""
        user.require_auth()
        self._require_initialized()
        self._require_not_locked()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        self._set_locked(True)

        staking_token = self.storage.get("staking_token")
        self.env.transfer(user, self.env.current_contract(), staking_token, amount)

        # Update staked balances
        user_bal = self.storage.get(f"user_staked:{user}", U128(0))
        new_user_bal = user_bal + amount
        self.storage.set(f"user_staked:{user}", new_user_bal)

        total_bal = self.storage.get("total_staked")
        new_total_bal = total_bal + amount
        self.storage.set("total_staked", new_total_bal)

        # Write checkpoints for current epoch
        current_epoch = self.storage.get("current_epoch")
        self._write_user_checkpoint(user, current_epoch, new_user_bal)
        self._write_total_checkpoint(current_epoch, new_total_bal)

        self._set_locked(False)

        self.env.emit_event("staked", {
            "user": user,
            "amount": amount,
            "new_balance": new_user_bal
        })

    @external
    def unstake(self, user: Address, amount: U128):
        """Unstake governance tokens and withdraw them."""
        user.require_auth()
        self._require_initialized()
        self._require_not_locked()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        self._set_locked(True)

        user_bal = self.storage.get(f"user_staked:{user}", U128(0))
        if user_bal < amount:
            raise ContractError.INSUFFICIENT_STAKE

        new_user_bal = user_bal - amount
        self.storage.set(f"user_staked:{user}", new_user_bal)

        total_bal = self.storage.get("total_staked")
        new_total_bal = total_bal - amount
        self.storage.set("total_staked", new_total_bal)

        # Write checkpoints
        current_epoch = self.storage.get("current_epoch")
        self._write_user_checkpoint(user, current_epoch, new_user_bal)
        self._write_total_checkpoint(current_epoch, new_total_bal)

        # Return tokens
        staking_token = self.storage.get("staking_token")
        self.env.transfer(self.env.current_contract(), user, staking_token, amount)

        self._set_locked(False)

        self.env.emit_event("unstaked", {
            "user": user,
            "amount": amount,
            "new_balance": new_user_bal
        })

    # ------------------------------------------------------------------ #
    #  Fee Collection and Epoch Finalization
    # ------------------------------------------------------------------ #

    @external
    def deposit_fees(self, caller: Address, token: Address, amount: U128):
        """
        Deposit protocol fees collected from exchanges, routers, or vaults.
        """
        caller.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        self.env.transfer(caller, self.env.current_contract(), token, amount)

        dist_token = self.storage.get("distribution_token")
        if token == dist_token:
            accum = self.storage.get("accumulated_fees")
            self.storage.set("accumulated_fees", accum + amount)

        self.env.emit_event("fees_deposited", {
            "sender": caller,
            "token": token,
            "amount": amount
        })

    @external
    def convert_fees(
        self,
        caller: Address,
        token_in: Address,
        pool: Address,
        pool_mode: U64,      # 1: Constant Product, 2: StableSwap
        min_amount_out: U128,
        deadline: U64,
    ) -> U128:
        """
        Convert alternative fee tokens into the primary distribution token (e.g. USDC).
        Admin or keeper only.
        """
        self._require_initialized()
        self._require_not_locked()
        self._check_deadline(deadline)

        dist_token = self.storage.get("distribution_token")
        if token_in == dist_token:
            raise ContractError.SWAP_FAILED

        self._set_locked(True)

        # Get actual contract balance of token_in
        balance_in = self.env.token(token_in).balance(self.env.current_contract())
        if balance_in == U128(0):
            raise ContractError.ZERO_AMOUNT

        # Execute swap via target pool
        actual_received = self._execute_pool_swap(
            pool, pool_mode, token_in, dist_token, balance_in, min_amount_out, deadline
        )

        # Accumulate converted output
        accum = self.storage.get("accumulated_fees")
        self.storage.set("accumulated_fees", accum + actual_received)

        self._set_locked(False)

        self.env.emit_event("fees_converted", {
            "token_in": token_in,
            "amount_in": balance_in,
            "received_dist": actual_received
        })
        return actual_received

    @external
    def checkpoint_epoch(self):
        """
        Finalize the current epoch fees and rollover to the next.
        Anyone can trigger once epoch duration elapses.
        """
        self._require_initialized()

        now = self.env.ledger().timestamp()
        last_check = self.storage.get("last_epoch_checkpoint_time")
        
        if now < last_check + EPOCH_DURATION:
            raise ContractError.EPOCH_NOT_ELAPSED

        current_epoch = self.storage.get("current_epoch")
        accum = self.storage.get("accumulated_fees")

        # Snapshot fee yield of this epoch
        self.storage.set(f"epoch_fee_yield:{current_epoch}", accum)
        
        # Reset accumulated accumulator
        self.storage.set("accumulated_fees", U128(0))
        
        # Roll over
        next_epoch = current_epoch + U64(1)
        self.storage.set("current_epoch", next_epoch)
        self.storage.set("last_epoch_checkpoint_time", now)

        # Carry forward staking checkpoint values to next epoch to preserve continuity
        total_staked = self.storage.get("total_staked")
        self._write_total_checkpoint(next_epoch, total_staked)

        self.env.emit_event("epoch_checkpoint", {
            "finalized_epoch": current_epoch,
            "fees_distributed": accum,
            "next_epoch": next_epoch,
            "timestamp": now
        })

    # ------------------------------------------------------------------ #
    #  Stakers Claims Logic
    # ------------------------------------------------------------------ #

    @external
    def claim_rewards(self, user: Address, start_epoch: U64, end_epoch: U64) -> U128:
        """
        Claim accumulated fee shares for epochs [start_epoch, end_epoch].
        Can only claim past finalized epochs.
        """
        user.require_auth()
        self._require_initialized()
        self._require_not_locked()

        current_epoch = self.storage.get("current_epoch")
        if start_epoch == U64(0) or end_epoch >= current_epoch or start_epoch > end_epoch:
            raise ContractError.INVALID_EPOCH_RANGE

        self._set_locked(True)

        total_payout = U128(0)

        for ep in range(int(start_epoch), int(end_epoch) + 1):
            epoch_id = U64(ep)
            claim_key = f"user_claimed:{epoch_id}:{user}"
            
            # Skip if already claimed
            if self.storage.get(claim_key, False):
                continue

            # Get user stake and total stake snapshot at epoch
            user_stake = self._get_user_stake_at_epoch(user, epoch_id)
            total_stake = self._get_total_stake_at_epoch(epoch_id)

            if user_stake > U128(0) and total_stake > U128(0):
                fee_yield = self.storage.get(f"epoch_fee_yield:{epoch_id}", U128(0))
                share = (user_stake * fee_yield) // total_stake
                total_payout += share
                self.storage.set(claim_key, True)

        if total_payout > U128(0):
            dist_token = self.storage.get("distribution_token")
            self.env.transfer(self.env.current_contract(), user, dist_token, total_payout)

        self._set_locked(False)

        self.env.emit_event("rewards_claimed", {
            "user": user,
            "payout": total_payout,
            "start_epoch": start_epoch,
            "end_epoch": end_epoch
        })
        return total_payout

    # ------------------------------------------------------------------ #
    #  View Functions
    # ------------------------------------------------------------------ #

    @view
    def get_current_epoch(self) -> U64:
        """Get the active epoch ID."""
        return self.storage.get("current_epoch", U64(0))

    @view
    def get_epoch_fee_yield(self, epoch_id: U64) -> U128:
        """Get finalized fees for an epoch."""
        return self.storage.get(f"epoch_fee_yield:{epoch_id}", U128(0))

    @view
    def is_epoch_claimed(self, user: Address, epoch_id: U64) -> Bool:
        """Check if user claimed rewards for a specific epoch."""
        return self.storage.get(f"user_claimed:{epoch_id}:{user}", False)

    @view
    def get_staked_balance(self, user: Address) -> U128:
        """Query user staked balance."""
        return self.storage.get(f"user_staked:{user}", U128(0))

    @view
    def get_total_staked(self) -> U128:
        """Query global staked total."""
        return self.storage.get("total_staked", U128(0))

    @view
    def get_epoch_info(self) -> Map:
        """Query time state of epoch."""
        now = self.env.ledger().timestamp()
        last_check = self.storage.get("last_epoch_checkpoint_time")
        next_check = last_check + EPOCH_DURATION

        return {
            "current_epoch": self.storage.get("current_epoch"),
            "last_checkpoint": last_check,
            "next_checkpoint": next_check,
            "time_remaining": next_check - now if next_check > now else U64(0),
            "accumulated_fees": self.storage.get("accumulated_fees")
        }

    # ------------------------------------------------------------------ #
    #  Historical Checkpoint Retrieval
    # ------------------------------------------------------------------ #

    def _write_user_checkpoint(self, user: Address, epoch: U64, balance: U128):
        """Append or update a stake checkpoint for a user."""
        checkpoints_key = f"checkpoints:{user}"
        checkpoints = self.storage.get(checkpoints_key, Vec())

        # If last entry was created in the same epoch, overwrite it
        if len(checkpoints) > 0:
            last_cp = checkpoints[len(checkpoints) - 1]
            if last_cp["epoch"] == epoch:
                last_cp["amount"] = balance
                checkpoints.set(len(checkpoints) - 1, last_cp)
                self.storage.set(checkpoints_key, checkpoints)
                return

        new_cp = {
            "epoch": epoch,
            "amount": balance
        }
        checkpoints.append(new_cp)
        self.storage.set(checkpoints_key, checkpoints)

    def _write_total_checkpoint(self, epoch: U64, balance: U128):
        """Append or update a global stake checkpoint."""
        checkpoints = self.storage.get("total_checkpoints")

        if len(checkpoints) > 0:
            last_cp = checkpoints[len(checkpoints) - 1]
            if last_cp["epoch"] == epoch:
                last_cp["amount"] = balance
                checkpoints.set(len(checkpoints) - 1, last_cp)
                self.storage.set("total_checkpoints", checkpoints)
                return

        new_cp = {
            "epoch": epoch,
            "amount": balance
        }
        checkpoints.append(new_cp)
        self.storage.set("total_checkpoints", checkpoints)

    def _get_user_stake_at_epoch(self, user: Address, epoch: U64) -> U128:
        """Historical lookup of user staked balance during epoch."""
        checkpoints = self.storage.get(f"checkpoints:{user}", Vec())
        return self._find_checkpoint_balance(checkpoints, epoch)

    def _get_total_stake_at_epoch(self, epoch: U64) -> U128:
        """Historical lookup of global staked total during epoch."""
        checkpoints = self.storage.get("total_checkpoints")
        return self._find_checkpoint_balance(checkpoints, epoch)

    def _find_checkpoint_balance(self, checkpoints: Vec, target_epoch: U64) -> U128:
        """Iterates checkpoints to find active balance during target_epoch."""
        if len(checkpoints) == 0:
            return U128(0)

        # Checkpoints list is sorted by epoch ascending
        # Find the checkpoint where checkpoint_epoch <= target_epoch with the largest epoch
        active_bal = U128(0)
        found = False

        for i in range(len(checkpoints)):
            cp = checkpoints[i]
            if cp["epoch"] <= target_epoch:
                active_bal = cp["amount"]
                found = True
            else:
                break # Since sorted ascending, all subsequent entries are > target_epoch

        return active_bal

    # ------------------------------------------------------------------ #
    #  Internal Helpers
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_not_locked(self):
        if self.storage.get("reentrancy_locked", False):
            raise ContractError.REENTRANCY_GUARD

    def _set_locked(self, locked: Bool):
        self.storage.set("reentrancy_locked", locked)

    def _check_deadline(self, deadline: U64):
        if self.env.ledger().timestamp() > deadline:
            raise ContractError.DEADLINE_EXPIRED

    def _execute_pool_swap(
        self,
        pool: Address,
        mode: U64,
        token_in: Address,
        token_out: Address,
        amount_in: U128,
        min_amount_out: U128,
        deadline: U64
    ) -> U128:
        """Swaps alternative tokens to primary distribution token on AMM/StableSwap."""
        if mode == U64(1):
            # Constant Product AMM swap_exact_input
            # Router swap call: we call the pool swap exact input
            # Because pool pulls token_in from this contract, we must authorize first.
            actual_received = self.env.invoke_contract(pool, "swap_exact_input", [
                self.env.current_contract(),
                token_in,
                amount_in,
                min_amount_out,
                deadline
            ])
            return actual_received

        elif mode == U64(2):
            # StableSwap swap(caller, i, j, dx, min_dy, deadline)
            pool_info = self.env.invoke_contract(pool, "get_pool_info", [])
            tokens = pool_info["tokens"]

            idx_in = -1
            idx_out = -1
            for idx in range(len(tokens)):
                if tokens[idx] == token_in:
                    idx_in = idx
                if tokens[idx] == token_out:
                    idx_out = idx

            if idx_in == -1 or idx_out == -1:
                raise ContractError.POOL_VALIDATION_FAILED

            actual_received = self.env.invoke_contract(pool, "swap", [
                self.env.current_contract(),
                U64(idx_in),
                U64(idx_out),
                amount_in,
                min_amount_out,
                deadline
            ])
            return actual_received

        else:
            raise ContractError.POOL_VALIDATION_FAILED
