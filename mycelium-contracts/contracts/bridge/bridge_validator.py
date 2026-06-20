"""
Bridge Validator Registry — Weight-based consensus and reward distribution.

Mycelium Smart Contract for Stellar. Tracks validator stakes, computes signature
consensus thresholds based on weight, enforces stake lockups, and distributes
accrued bridge fees proportionally to staked validators.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)

class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    PAUSED = 4
    INSUFFICIENT_STAKE = 5
    INVALID_THRESHOLD_PCT = 6
    LOCKUP_NOT_EXPIRED = 7
    INVALID_REQUEST = 8
    NO_REWARDS = 9
    DUPLICATE_VALIDATOR = 10
    ZERO_STAKE = 11

@contract
class BridgeValidator:
    """
    Validator registry and stake weight tracker for bridge consensus.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        staking_token: Address,
        min_stake: U128,
        lockup_duration: U64,  # Time in seconds validators must wait to unstake
        threshold_pct: U64      # Percentage of total weight needed (e.g. 67 for 67%)
    ):
        """Initialize the validator registry contract configurations."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if threshold_pct < U64(50) or threshold_pct > U64(100):
            raise ContractError.INVALID_THRESHOLD_PCT

        self.storage.set("admin", admin)
        self.storage.set("staking_token", staking_token)
        self.storage.set("min_stake", min_stake)
        self.storage.set("lockup_duration", lockup_duration)
        self.storage.set("threshold_pct", threshold_pct)
        self.storage.set("total_stake", U128(0))
        self.storage.set("paused", False)

        # Rewards tracking variables (similar to ERC-900 / synthetics staking)
        self.storage.set("acc_reward_per_share", U128(0))
        self.storage.set("reward_multiplier", U128(1_000_000_000_000)) # Precision scaling

        # Keep count of validators
        self.storage.set("validator_count", U64(0))

        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "staking_token": staking_token,
            "min_stake": min_stake,
            "threshold_pct": threshold_pct
        })

    @external
    def stake(self, caller: Address, amount: U128):
        """
        Stake tokens to register or increase validation weight.
        Automatically updates accrued rewards before altering stakes.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if amount == U128(0):
            raise ContractError.ZERO_STAKE

        token = self.storage.get("staking_token")
        contract_addr = self.env.current_contract_address()

        # Harvest pending rewards first
        self._update_rewards(caller)

        # Transfer tokens in
        self.env.call(token, "transfer", caller, contract_addr, amount)

        # Update stake
        old_stake = self.storage.get(f"stake_{caller}", U128(0))
        new_stake = old_stake + amount
        self.storage.set(f"stake_{caller}", new_stake)

        # Track registry lists
        if old_stake == U128(0):
            # New validator joining
            count = self.storage.get("validator_count", U64(0))
            self.storage.set(f"validator_addr_{count}", caller)
            self.storage.set(f"validator_index_{caller}", count)
            self.storage.set("validator_count", count + U64(1))

        total_stake = self.storage.get("total_stake", U128(0))
        self.storage.set("total_stake", total_stake + amount)

        # Update reward debt
        self._reset_reward_debt(caller, new_stake)

        self.env.emit_event("validator_staked", {
            "validator": caller,
            "amount": amount,
            "total_stake": new_stake
        })

    @external
    def unstake_request(self, caller: Address, amount: U128) -> U64:
        """
        Initiate unstaking. Establishes a lockup timer before release.
        """
        caller.require_auth()
        self._require_initialized()

        stake = self.storage.get(f"stake_{caller}", U128(0))
        if amount == U128(0) or amount > stake:
            raise ContractError.INSUFFICIENT_STAKE

        self._update_rewards(caller)

        # Update stake details
        new_stake = stake - amount
        self.storage.set(f"stake_{caller}", new_stake)

        total_stake = self.storage.get("total_stake", U128(0))
        self.storage.set("total_stake", total_stake - amount)

        self._reset_reward_debt(caller, new_stake)

        # Create lockup request
        now = self._get_now()
        lockup_dur = self.storage.get("lockup_duration", U64(0))
        unlock_time = now + lockup_dur

        request_id = self.storage.get(f"unstake_req_count_{caller}", U64(0))
        self.storage.set(f"unstake_req_amt_{caller}_{request_id}", amount)
        self.storage.set(f"unstake_req_time_{caller}_{request_id}", unlock_time)
        self.storage.set(f"unstake_req_claimed_{caller}_{request_id}", False)
        self.storage.set(f"unstake_req_count_{caller}", request_id + U64(1))

        # Check if validator should be cleaned from index count
        if new_stake == U128(0):
            self._remove_validator_from_list(caller)

        self.env.emit_event("unstake_requested", {
            "validator": caller,
            "amount": amount,
            "unlock_time": unlock_time,
            "request_id": request_id
        })

        return request_id

    @external
    def unstake_execute(self, caller: Address, request_id: U64):
        """
        Withdraw stake tokens once lockup has expired.
        """
        caller.require_auth()
        self._require_initialized()

        unlock_time = self.storage.get(f"unstake_req_time_{caller}_{request_id}")
        if unlock_time is None:
            raise ContractError.INVALID_REQUEST

        if self._get_now() < unlock_time:
            raise ContractError.LOCKUP_NOT_EXPIRED

        if self.storage.get(f"unstake_req_claimed_{caller}_{request_id}", False):
            raise ContractError.INVALID_REQUEST

        amount = self.storage.get(f"unstake_req_amt_{caller}_{request_id}", U128(0))
        self.storage.set(f"unstake_req_claimed_{caller}_{request_id}", True)

        # Transfer tokens back
        token = self.storage.get("staking_token")
        contract_addr = self.env.current_contract_address()
        self.env.call(token, "transfer", contract_addr, caller, amount)

        self.env.emit_event("unstake_completed", {
            "validator": caller,
            "amount": amount,
            "request_id": request_id
        })

    @external
    def deposit_rewards(self, caller: Address, amount: U128):
        """
        Deposit rewards (bridge fees) to be split among active validators.
        """
        caller.require_auth()
        self._require_initialized()

        total_stake = self.storage.get("total_stake", U128(0))
        if total_stake == U128(0):
            # No validators to receive, transfer still succeeds or returns error
            raise ContractError.ZERO_STAKE

        token = self.storage.get("staking_token")
        contract_addr = self.env.current_contract_address()

        self.env.call(token, "transfer", caller, contract_addr, amount)

        # Update accumulator: acc = acc + (amount * multiplier) / total_stake
        acc = self.storage.get("acc_reward_per_share", U128(0))
        multiplier = self.storage.get("reward_multiplier", U128(1))
        added_share = (amount * multiplier) / total_stake
        self.storage.set("acc_reward_per_share", acc + added_share)

        self.env.emit_event("rewards_deposited", {
            "depositor": caller,
            "amount": amount
        })

    @external
    def claim_rewards(self, caller: Address):
        """
        Withdraw accumulated consensus rewards.
        """
        caller.require_auth()
        self._require_initialized()

        self._update_rewards(caller)

        pending = self.storage.get(f"pending_rewards_{caller}", U128(0))
        if pending == U128(0):
            raise ContractError.NO_REWARDS

        self.storage.set(f"pending_rewards_{caller}", U128(0))

        token = self.storage.get("staking_token")
        contract_addr = self.env.current_contract_address()
        self.env.call(token, "transfer", contract_addr, caller, pending)

        self.env.emit_event("rewards_claimed", {
            "validator": caller,
            "amount": pending
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause validator staking activities (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def verify_signatures_threshold(
        self,
        message: Bytes,
        validators: Vec, # Vec of Bytes (pubkeys)
        signatures: Vec  # Vec of Bytes (sigs)
    ) -> Bool:
        """
        Verify that a list of signatures satisfies consensus weight threshold.
        Can be called by other bridge contracts to check if state transmission is valid.
        """
        if not self.storage.get("initialized", False):
            return False

        total_stake = self.storage.get("total_stake", U128(0))
        if total_stake == U128(0):
            return False

        accumulated_weight = U128(0)
        used_validators = Map(self.env)

        for i in range(len(signatures)):
            sig = signatures.get(i)
            validator_pubkey = validators.get(i)

            # Look up Stellar Address for this validator public key
            # In a simplified implementation, we map validator public key (Bytes) to their Address
            # Let's check if this validator pubkey has a registered address in storage
            validator_addr = self.storage.get(f"addr_from_pubkey_{validator_pubkey}")
            if validator_addr is None:
                continue

            if used_validators.get(validator_addr, False):
                continue

            # Verify signature using ed25519
            if self.env.crypto().verify_sig_ed25519(validator_pubkey, message, sig):
                used_validators.set(validator_addr, True)
                weight = self.storage.get(f"stake_{validator_addr}", U128(0))
                accumulated_weight += weight

        # Calculate percentage weight met
        threshold_pct = self.storage.get("threshold_pct", U64(0))
        required_weight = (total_stake * U128(threshold_pct)) / U128(100)

        return accumulated_weight >= required_weight

    @external
    def register_validator_pubkey(self, caller: Address, pubkey: Bytes):
        """Bind validator's consensus public key to their Stellar account address."""
        caller.require_auth()
        self._require_initialized()

        self.storage.set(f"addr_from_pubkey_{pubkey}", caller)
        self.storage.set(f"pubkey_from_addr_{caller}", pubkey)

        self.env.emit_event("pubkey_registered", {
            "validator": caller,
            "pubkey": pubkey
        })

    @view
    def get_validator_info(self, validator: Address) -> Map:
        """Retrieve staking and pending reward info for a validator."""
        res = Map(self.env)
        stake = self.storage.get(f"stake_{validator}", U128(0))
        
        # Calculate pending rewards: stake * (acc - debt) / multiplier + pending
        acc = self.storage.get("acc_reward_per_share", U128(0))
        debt = self.storage.get(f"reward_debt_{validator}", U128(0))
        pending_saved = self.storage.get(f"pending_rewards_{validator}", U128(0))
        multiplier = self.storage.get("reward_multiplier", U128(1))
        
        pending = U128(0)
        if stake > U128(0):
            pending = ((stake * (acc - debt)) / multiplier) + pending_saved
        else:
            pending = pending_saved

        res.set("stake", stake)
        res.set("pending_rewards", pending)
        res.set("pubkey", self.storage.get(f"pubkey_from_addr_{validator}"))
        return res

    @view
    def get_total_consensus_weight(self) -> U128:
        """Query total stake sum contributing to consensus weight."""
        return self.storage.get("total_stake", U128(0))

    # --- INTERNAL HELPERS ---

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_not_paused(self):
        if self.storage.get("paused", False):
            raise ContractError.PAUSED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _get_now(self) -> U64:
        return self.env.ledger_timestamp()

    def _update_rewards(self, validator: Address):
        """Update rewards for validator using the accumulative index mechanism."""
        stake = self.storage.get(f"stake_{validator}", U128(0))
        acc = self.storage.get("acc_reward_per_share", U128(0))
        debt = self.storage.get(f"reward_debt_{validator}", U128(0))
        multiplier = self.storage.get("reward_multiplier", U128(1))
        pending_saved = self.storage.get(f"pending_rewards_{validator}", U128(0))

        if stake > U128(0):
            accumulated = (stake * (acc - debt)) / multiplier
            self.storage.set(f"pending_rewards_{validator}", pending_saved + accumulated)

        self.storage.set(f"reward_debt_{validator}", acc)

    def _reset_reward_debt(self, validator: Address, stake: U128):
        """Sync validator's reward debt after a change in stake weight."""
        acc = self.storage.get("acc_reward_per_share", U128(0))
        self.storage.set(f"reward_debt_{validator}", acc)

    def _remove_validator_from_list(self, validator: Address):
        """Clean validator entry from the validator index tracker."""
        idx = self.storage.get(f"validator_index_{validator}")
        if idx is not None:
            count = self.storage.get("validator_count", U64(0))
            last_idx = count - U64(1)
            last_validator = self.storage.get(f"validator_addr_{last_idx}")

            self.storage.set(f"validator_addr_{idx}", last_validator)
            self.storage.set(f"validator_index_{last_validator}", idx)

            self.storage.remove(f"validator_addr_{last_idx}")
            self.storage.remove(f"validator_index_{validator}")
            self.storage.set("validator_count", last_idx)
