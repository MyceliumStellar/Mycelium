"""
Token Vesting — Schedule-based token vesting with cliff and revocation.

Mycelium Smart Contract for Stellar
Enforces linear token release schedules with custom cliff durations. Admin can register schedules
for multiple beneficiaries, revoke revocable vesting agreements, and trigger accelerated payouts.
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
    SCHEDULE_ALREADY_EXISTS = 5
    SCHEDULE_NOT_FOUND = 6
    SCHEDULE_REVOKED = 7
    NOT_REVOCABLE = 8
    NO_VESTED_TOKENS = 9
    ACCELERATION_EXCEEDS_UNVESTED = 10


@contract
class TokenVesting:
    """
    Manages token allocation schedules, distributing vesting tokens linearly after a cliff period,
    supporting administration hooks for revocation and schedule acceleration.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, vesting_token: Address):
        """Initialize the Token Vesting contract."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("vesting_token", vesting_token)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "vesting_token": vesting_token,
        })

    @external
    def register_vesting_schedule(
        self,
        admin: Address,
        beneficiary: Address,
        start_time: U64,
        cliff_duration: U64,
        duration: U64,
        total_amount: U128,
        revocable: Bool,
    ):
        """Register a vesting schedule for a beneficiary."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        if self.storage.get(f"schedule:exists:{beneficiary}", False):
            raise ContractError.SCHEDULE_ALREADY_EXISTS

        # Safety checks on inputs
        if duration == 0 or cliff_duration > duration or total_amount == 0:
            raise ContractError.INVALID_PARAMETERS

        # Transfer the tokens to be vested from admin to the contract
        vesting_token = self.storage.get("vesting_token")
        self.env.transfer(vesting_token, admin, self.env.current_contract(), total_amount)

        self.storage.set(f"schedule:exists:{beneficiary}", True)
        self.storage.set(f"schedule:start_time:{beneficiary}", start_time)
        self.storage.set(f"schedule:cliff:{beneficiary}", cliff_duration)
        self.storage.set(f"schedule:duration:{beneficiary}", duration)
        self.storage.set(f"schedule:total:{beneficiary}", total_amount)
        self.storage.set(f"schedule:released:{beneficiary}", U128(0))
        self.storage.set(f"schedule:revocable:{beneficiary}", revocable)
        self.storage.set(f"schedule:revoked:{beneficiary}", False)

        self.env.emit_event("schedule_registered", {
            "beneficiary": beneficiary,
            "start_time": start_time,
            "cliff_duration": cliff_duration,
            "duration": duration,
            "total_amount": total_amount,
            "revocable": revocable,
        })

    @external
    def release(self, beneficiary: Address):
        """Release vested tokens for the beneficiary."""
        self._require_initialized()

        if not self.storage.get(f"schedule:exists:{beneficiary}", False):
            raise ContractError.SCHEDULE_NOT_FOUND

        if self.storage.get(f"schedule:revoked:{beneficiary}", False):
            # If revoked, it can still claim any remaining vested tokens up to the revocation event
            # which were locked during revocation. Let's process normally since 'total' is capped.
            pass

        vested = self._calculate_vested_amount(beneficiary)
        released = self.storage.get(f"schedule:released:{beneficiary}")
        
        claimable = vested - released
        if claimable == 0:
            raise ContractError.NO_VESTED_TOKENS

        # Update released tokens
        self.storage.set(f"schedule:released:{beneficiary}", released + claimable)

        # Transfer tokens to beneficiary
        vesting_token = self.storage.get("vesting_token")
        self.env.transfer(vesting_token, self.env.current_contract(), beneficiary, claimable)

        self.env.emit_event("tokens_released", {
            "beneficiary": beneficiary,
            "amount": claimable,
        })

    @external
    def revoke(self, admin: Address, beneficiary: Address):
        """Revoke the vesting schedule for a beneficiary, returning unvested tokens to the admin."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        if not self.storage.get(f"schedule:exists:{beneficiary}", False):
            raise ContractError.SCHEDULE_NOT_FOUND

        if self.storage.get(f"schedule:revoked:{beneficiary}", False):
            raise ContractError.SCHEDULE_REVOKED

        if not self.storage.get(f"schedule:revocable:{beneficiary}", False):
            raise ContractError.NOT_REVOCABLE

        # Calculate vested amount up to this exact moment
        vested = self._calculate_vested_amount(beneficiary)
        total = self.storage.get(f"schedule:total:{beneficiary}")
        released = self.storage.get(f"schedule:released:{beneficiary}")

        # Unvested tokens go back to admin
        unvested = total - vested

        # Mark as revoked and adjust total to the vested amount
        self.storage.set(f"schedule:revoked:{beneficiary}", True)
        self.storage.set(f"schedule:total:{beneficiary}", vested)

        # Release any vested but unreleased tokens to the beneficiary
        claimable = vested - released
        vesting_token = self.storage.get("vesting_token")

        if claimable > 0:
            self.storage.set(f"schedule:released:{beneficiary}", vested)
            self.env.transfer(vesting_token, self.env.current_contract(), beneficiary, claimable)

        # Return unvested tokens to admin
        if unvested > 0:
            self.env.transfer(vesting_token, self.env.current_contract(), admin, unvested)

        self.env.emit_event("schedule_revoked", {
            "beneficiary": beneficiary,
            "refunded_amount": unvested,
            "final_vested_amount": vested,
        })

    @external
    def accelerate_payout(self, admin: Address, beneficiary: Address, amount: U128):
        """Admin accelerates payout, instantly releasing a set amount of unvested tokens."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        if not self.storage.get(f"schedule:exists:{beneficiary}", False):
            raise ContractError.SCHEDULE_NOT_FOUND

        if self.storage.get(f"schedule:revoked:{beneficiary}", False):
            raise ContractError.SCHEDULE_REVOKED

        # Calculate unvested amount
        vested = self._calculate_vested_amount(beneficiary)
        total = self.storage.get(f"schedule:total:{beneficiary}")
        unvested = total - vested

        if amount > unvested:
            raise ContractError.ACCELERATION_EXCEEDS_UNVESTED

        # To accelerate, we increase the user's released amount and transfer tokens.
        # But we must also update the schedule parameters to reflect that these tokens have been payout early.
        # The simplest way is to increase the 'released' amount and decrease the 'total' amount by the accelerated
        # amount, or we can just shift the 'released' count.
        # Actually, if we just transfer it, we must make sure they don't vest it *again*.
        # So we decrease the 'total' amount and also record this as payout.
        # Better: we adjust 'total' down and send the payout, meaning we remove 'amount' from their remaining schedule.
        # Total decreases by 'amount'. Their current vested amount also needs to be adjusted so that it doesn't double-claim.
        # Let's reduce 'total' by 'amount' and transfer it to the beneficiary.
        self.storage.set(f"schedule:total:{beneficiary}", total - amount)

        vesting_token = self.storage.get("vesting_token")
        self.env.transfer(vesting_token, self.env.current_contract(), beneficiary, amount)

        self.env.emit_event("payout_accelerated", {
            "beneficiary": beneficiary,
            "accelerated_amount": amount,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_vesting_schedule(self, beneficiary: Address) -> Map:
        """Retrieve details of a vesting schedule."""
        if not self.storage.get(f"schedule:exists:{beneficiary}", False):
            raise ContractError.SCHEDULE_NOT_FOUND
        return {
            "start_time": self.storage.get(f"schedule:start_time:{beneficiary}"),
            "cliff_duration": self.storage.get(f"schedule:cliff:{beneficiary}"),
            "duration": self.storage.get(f"schedule:duration:{beneficiary}"),
            "total_amount": self.storage.get(f"schedule:total:{beneficiary}"),
            "released_amount": self.storage.get(f"schedule:released:{beneficiary}"),
            "revocable": self.storage.get(f"schedule:revocable:{beneficiary}"),
            "revoked": self.storage.get(f"schedule:revoked:{beneficiary}"),
        }

    @view
    def get_claimable_amount(self, beneficiary: Address) -> U128:
        """Get the amount of tokens currently vested and claimable by the beneficiary."""
        if not self.storage.get(f"schedule:exists:{beneficiary}", False):
            return U128(0)

        vested = self._calculate_vested_amount(beneficiary)
        released = self.storage.get(f"schedule:released:{beneficiary}", U128(0))
        return vested - released

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _calculate_vested_amount(self, beneficiary: Address) -> U128:
        start_time = self.storage.get(f"schedule:start_time:{beneficiary}")
        cliff_duration = self.storage.get(f"schedule:cliff:{beneficiary}")
        duration = self.storage.get(f"schedule:duration:{beneficiary}")
        total_amount = self.storage.get(f"schedule:total:{beneficiary}")

        current_time = self.env.ledger().timestamp()

        # 1. Before start time or cliff: 0 vested
        if current_time < start_time + cliff_duration:
            return U128(0)

        # 2. After vesting duration: 100% vested
        if current_time >= start_time + duration:
            return total_amount

        # 3. Linear vesting between start + cliff and end
        elapsed = current_time - start_time
        vested = (total_amount * U128(elapsed)) // U128(duration)
        return vested
