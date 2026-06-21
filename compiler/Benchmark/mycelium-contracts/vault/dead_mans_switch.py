"""
Dead Man's Switch — Inactivity-triggered inheritance/backup recovery vault.

Mycelium Smart Contract for Stellar
Protects assets by requiring the owner to perform regular heartbeat check-ins.
If the owner is inactive for a set duration, beneficiaries can declare death, triggering
an execution timelock. The owner can cancel the declaration at any time during the timelock.
If the timelock expires without a reset, funds are distributed to beneficiaries.
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
    SWITCH_NOT_TRIGGERABLE = 5
    TIMELOCK_ACTIVE = 6
    TIMELOCK_NOT_EXPIRED = 7
    DEATH_ALREADY_DECLARED = 8
    NOT_DECLARED = 9


@contract
class DeadMansSwitch:
    """
    Asset preservation lockup that transfers ownership to designated heirs/backup keys
    following owner inactivity, using a grace period reset mechanism.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        owner: Address,
        inactivity_period: U64,          # In seconds (e.g. 180 days = 15552000)
        execution_timelock_period: U64,  # In seconds (e.g. 30 days = 2592000)
        beneficiaries: Vec,
        shares_bps: Vec,
    ):
        """Initialize the dead man's switch contract."""
        owner.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if len(beneficiaries) != len(shares_bps) or len(beneficiaries) == 0:
            raise ContractError.INVALID_PARAMETERS

        if inactivity_period < 86400 or execution_timelock_period < 86400:
            raise ContractError.INVALID_PARAMETERS

        total_bps = U64(0)
        for i in range(len(shares_bps)):
            b = beneficiaries[i]
            s = shares_bps[i]
            if s == 0 or b == owner:
                raise ContractError.INVALID_PARAMETERS
            total_bps = total_bps + s

        if total_bps != 10000:
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("owner", owner)
        self.storage.set("inactivity_period", inactivity_period)
        self.storage.set("execution_timelock_period", execution_timelock_period)
        self.storage.set("last_heartbeat", self.env.ledger().timestamp())

        # Save beneficiaries
        self.storage.set("beneficiary_count", U64(len(beneficiaries)))
        for i in range(len(beneficiaries)):
            b = beneficiaries[i]
            s = shares_bps[i]
            self.storage.set(f"beneficiary:index:{i}", b)
            self.storage.set(f"beneficiary:bps:{b}", s)
            self.storage.set(f"beneficiary:exists:{b}", True)

        self.storage.set("token_count", U64(0))
        self.storage.set("death_declared", False)
        self.storage.set("death_declared_time", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "owner": owner,
            "inactivity_period": inactivity_period,
            "timelock_period": execution_timelock_period,
        })

    @external
    def heartbeat(self, owner: Address):
        """Owner checks in, resetting the inactivity timer and canceling any pending death declarations."""
        owner.require_auth()
        self._require_initialized()

        active_owner = self.storage.get("owner")
        if owner != active_owner:
            raise ContractError.UNAUTHORIZED

        self.storage.set("last_heartbeat", self.env.ledger().timestamp())

        # If a beneficiary declared death, cancel it
        if self.storage.get("death_declared", False):
            self.storage.set("death_declared", False)
            self.storage.set("death_declared_time", U64(0))
            self.env.emit_event("death_declaration_canceled", {"by": owner})

        self.env.emit_event("heartbeat_reset", {"time": self.env.ledger().timestamp()})

    @external
    def deposit_asset(self, caller: Address, token: Address, amount: U128):
        """Deposit assets into the switch vault to be inherited/distributed later."""
        caller.require_auth()
        self._require_initialized()

        if amount == 0:
            raise ContractError.INVALID_PARAMETERS

        # Transfer tokens to this contract
        self.env.transfer(token, caller, self.env.current_contract(), amount)

        # Track registered tokens
        if not self.storage.get(f"token:registered:{token}", False):
            self.storage.set(f"token:registered:{token}", True)
            count = self.storage.get("token_count", U64(0))
            self.storage.set(f"token:index:{count}", token)
            self.storage.set("token_count", count + U64(1))

        # Update deposit balance
        bal = self.storage.get(f"token:deposited:{token}", U128(0))
        self.storage.set(f"token:deposited:{token}", bal + amount)

        self.env.emit_event("asset_deposited", {
            "token": token,
            "amount": amount,
            "by": caller,
        })

    @external
    def declare_death(self, caller: Address):
        """A beneficiary declares switch inactivity, beginning the execution grace timelock."""
        caller.require_auth()
        self._require_initialized()

        # Caller must be a registered beneficiary
        if not self.storage.get(f"beneficiary:exists:{caller}", False):
            raise ContractError.UNAUTHORIZED

        if self.storage.get("death_declared", False):
            raise ContractError.DEATH_ALREADY_DECLARED

        # Check inactivity period has elapsed
        last_heartbeat = self.storage.get("last_heartbeat")
        inactivity_period = self.storage.get("inactivity_period")
        current_time = self.env.ledger().timestamp()

        if current_time <= last_heartbeat + inactivity_period:
            raise ContractError.SWITCH_NOT_TRIGGERABLE

        # Mark death declared and start execution timelock
        self.storage.set("death_declared", True)
        self.storage.set("death_declared_time", current_time)
        self.storage.set("death_declared_by", caller)

        self.env.emit_event("death_declared", {
            "declared_by": caller,
            "declaration_time": current_time,
        })

    @external
    def execute_switch(self, caller: Address):
        """Distribute switch assets to beneficiaries after the grace period timelock expires."""
        self._require_initialized()

        if not self.storage.get("death_declared", False):
            raise ContractError.NOT_DECLARED

        death_declared_time = self.storage.get("death_declared_time")
        timelock = self.storage.get("execution_timelock_period")
        current_time = self.env.ledger().timestamp()

        if current_time <= death_declared_time + timelock:
            raise ContractError.TIMELOCK_NOT_EXPIRED

        # Distribute all tokens
        token_count = self.storage.get("token_count", U64(0))
        beneficiary_count = self.storage.get("beneficiary_count", U64(0))

        for t in range(token_count):
            token = self.storage.get(f"token:index:{t}")
            total_bal = self.storage.get(f"token:deposited:{token}", U128(0))

            if total_bal > 0:
                for b in range(beneficiary_count):
                    beneficiary = self.storage.get(f"beneficiary:index:{b}")
                    bps = self.storage.get(f"beneficiary:bps:{beneficiary}", U64(0))

                    payout = (total_bal * U128(bps)) // U128(10000)
                    if payout > 0:
                        self.env.transfer(token, self.env.current_contract(), beneficiary, payout)

                # Reset deposit tracker
                self.storage.set(f"token:deposited:{token}", U128(0))

        # Reset states to avoid duplicate executions
        self.storage.set("death_declared", False)
        self.storage.set("death_declared_time", U64(0))
        # Push last heartbeat into future to lock switch
        self.storage.set("last_heartbeat", U64(18446744073709551615))  # max u64

        self.env.emit_event("switch_executed", {"time": current_time})

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_status(self) -> Map:
        """Query switch state and timing check."""
        current_time = self.env.ledger().timestamp()
        last_heartbeat = self.storage.get("last_heartbeat", U64(0))
        inactivity = self.storage.get("inactivity_period", U64(0))
        
        is_triggerable = current_time > last_heartbeat + inactivity
        
        return {
            "last_heartbeat": last_heartbeat,
            "is_triggerable": is_triggerable,
            "death_declared": self.storage.get("death_declared"),
            "death_declared_time": self.storage.get("death_declared_time"),
            "time_remaining_until_trigger": U64(0) if is_triggerable else last_heartbeat + inactivity - current_time,
        }

    @view
    def get_beneficiaries(self) -> Vec:
        """Get registered beneficiaries list."""
        lst = Vec()
        count = self.storage.get("beneficiary_count", U64(0))
        for i in range(count):
            lst.append(self.storage.get(f"beneficiary:index:{i}"))
        return lst

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
