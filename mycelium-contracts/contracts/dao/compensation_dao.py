"""
Compensation DAO — Stream contributor pay per-second, performance reviews, vesting, and offboarding.

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
    STREAM_NOT_FOUND = 4
    VESTING_NOT_FOUND = 5
    INSUFFICIENT_FUNDS = 6
    CLIFF_NOT_REACHED = 7
    INVALID_STREAM_PARAMS = 8
    STREAM_INACTIVE = 9
    STREAM_ACTIVE = 10
    VESTING_ACTIVE = 11


class StreamStatus:
    ACTIVE = 0
    PAUSED = 1
    TERMINATED = 2


@contract
class CompensationDAO:
    """A contract to stream rewards to contributors, manage token vesting, and handle offboarding."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, deposit_token: Address):
        """Initialize the Compensation DAO.

        Args:
            admin: Admin address with permission to add streams/vesting and write reviews.
            deposit_token: Asset token used for payroll streams and vesting.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("token", deposit_token)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "token": deposit_token,
        })

    @external
    def create_stream(
        self,
        admin: Address,
        contributor: Address,
        rate_per_second: U128,
        start_time: U64,
        stop_time: U64,
    ):
        """Open a new per-second compensation stream for a contributor. Only admin.

        Args:
            admin: Admin address.
            contributor: Target contributor address.
            rate_per_second: Tokens streaming per second.
            start_time: Start timestamp of the stream.
            stop_time: Final timestamp of the stream.
        """
        self._require_initialized()
        self._require_admin(admin)

        if stop_time <= start_time or rate_per_second == U128(0):
            raise ContractError.INVALID_STREAM_PARAMS

        # Ensure no active stream already exists
        existing = self.storage.get(("stream", contributor), None)
        if existing is not None and existing["status"] == StreamStatus.ACTIVE:
            raise ContractError.STREAM_ACTIVE

        stream = {
            "contributor": contributor,
            "rate_per_second": rate_per_second,
            "start_time": start_time,
            "stop_time": stop_time,
            "last_claim_time": start_time,
            "status": StreamStatus.ACTIVE,
        }

        self.storage.set(("stream", contributor), stream)

        self.env.emit_event("stream_created", {
            "contributor": contributor,
            "rate_per_second": rate_per_second,
            "start_time": start_time,
            "stop_time": stop_time,
        })

    @external
    def claim_stream(self, contributor: Address):
        """Claim accumulated streaming compensation. Anyone can trigger for contributor.

        Args:
            contributor: Contributor address.
        """
        self._require_initialized()
        contributor.require_auth()

        stream = self.storage.get(("stream", contributor), None)
        if stream is None:
            raise ContractError.STREAM_NOT_FOUND
        if stream["status"] != StreamStatus.ACTIVE:
            raise ContractError.STREAM_INACTIVE

        now = self.env.ledger().timestamp()
        claim_limit = now
        if claim_limit > stream["stop_time"]:
            claim_limit = stream["stop_time"]

        last_claim = stream["last_claim_time"]
        if claim_limit <= last_claim:
            return # Nothing to claim

        elapsed = claim_limit - last_claim
        accumulated = elapsed * stream["rate_per_second"]

        stream["last_claim_time"] = claim_limit
        self.storage.set(("stream", contributor), stream)

        if accumulated > U128(0):
            token = self.storage.get("token")
            success = self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), contributor, accumulated])
            if not success:
                raise ContractError.INSUFFICIENT_FUNDS

        self.env.emit_event("stream_claimed", {
            "contributor": contributor,
            "claimed_amount": accumulated,
            "last_claim": claim_limit,
        })

    @external
    def create_vesting_schedule(
        self,
        admin: Address,
        beneficiary: Address,
        total_amount: U128,
        start_time: U64,
        cliff_time: U64,
        end_time: U64,
    ):
        """Create a token vesting schedule. Only admin.

        Args:
            admin: Admin address.
            beneficiary: Vesting recipient.
            total_amount: Total tokens to vest.
            start_time: Vesting start timestamp.
            cliff_time: Vesting cliff timestamp.
            end_time: Vesting end timestamp.
        """
        self._require_initialized()
        self._require_admin(admin)

        if start_time > cliff_time or cliff_time > end_time or total_amount == U128(0):
            raise ContractError.INVALID_STREAM_PARAMS

        existing = self.storage.get(("vesting", beneficiary), None)
        if existing is not None:
            raise ContractError.VESTING_ACTIVE

        vesting = {
            "beneficiary": beneficiary,
            "total_amount": total_amount,
            "start_time": start_time,
            "cliff_time": cliff_time,
            "end_time": end_time,
            "claimed_amount": U128(0),
            "revoked": False,
        }

        self.storage.set(("vesting", beneficiary), vesting)

        self.env.emit_event("vesting_schedule_created", {
            "beneficiary": beneficiary,
            "total_amount": total_amount,
            "cliff_time": cliff_time,
            "end_time": end_time,
        })

    @external
    def claim_vesting(self, beneficiary: Address):
        """Claim vested tokens. Beneficiary must authorize.

        Args:
            beneficiary: Beneficiary address.
        """
        self._require_initialized()
        beneficiary.require_auth()

        vesting = self.storage.get(("vesting", beneficiary), None)
        if vesting is None:
            raise ContractError.VESTING_NOT_FOUND
        if vesting["revoked"]:
            raise ContractError.STREAM_INACTIVE

        now = self.env.ledger().timestamp()
        if now < vesting["cliff_time"]:
            raise ContractError.CLIFF_NOT_REACHED

        claimable = self._calculate_vested_amount(vesting, now)
        if claimable == U128(0):
            return

        vesting["claimed_amount"] = vesting["claimed_amount"] + claimable
        self.storage.set(("vesting", beneficiary), vesting)

        token = self.storage.get("token")
        success = self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), beneficiary, claimable])
        if not success:
            raise ContractError.INSUFFICIENT_FUNDS

        self.env.emit_event("vesting_claimed", {
            "beneficiary": beneficiary,
            "amount": claimable,
        })

    @external
    def submit_performance_review(
        self,
        admin: Address,
        contributor: Address,
        score: U64,
        notes: Symbol,
    ):
        """Submit performance rating (1-5). Can grant a bonus or adjustment. Only admin.

        Args:
            admin: Admin address.
            contributor: Evaluated contributor.
            score: Rating score from 1 to 5.
            notes: Performance notes.
        """
        self._require_initialized()
        self._require_admin(admin)

        if score < U64(1) or score > U64(5):
            raise ContractError.INVALID_STREAM_PARAMS

        # Save review details
        rev_idx = self.storage.get(("review_count", contributor), U64(0)) + U64(1)
        self.storage.set(("review_count", contributor), rev_idx)

        self.storage.set(("review", contributor, rev_idx), {
            "score": score,
            "notes": notes,
            "timestamp": self.env.ledger().timestamp(),
        })

        # Apply adjustments:
        # Score 5: 10% rate boost to stream if active
        stream = self.storage.get(("stream", contributor), None)
        if stream is not None and stream["status"] == StreamStatus.ACTIVE:
            if score == U64(5):
                old_rate = stream["rate_per_second"]
                new_rate = (old_rate * U128(110)) / U128(100)
                stream["rate_per_second"] = new_rate
                self.storage.set(("stream", contributor), stream)
                self.env.emit_event("stream_rate_boosted", {
                    "contributor": contributor,
                    "new_rate": new_rate,
                })

        self.env.emit_event("review_submitted", {
            "contributor": contributor,
            "review_index": rev_idx,
            "score": score,
        })

    @external
    def terminate_and_offboard(self, admin: Address, contributor: Address):
        """Offboard contributor, stop stream immediately, and revoke unvested tokens. Only admin.

        Args:
            admin: Admin address.
            contributor: Terminated contributor.
        """
        self._require_initialized()
        self._require_admin(admin)

        # 1. Terminate stream
        stream = self.storage.get(("stream", contributor), None)
        if stream is not None and stream["status"] == StreamStatus.ACTIVE:
            # First trigger a final claim up to now
            now = self.env.ledger().timestamp()
            claim_limit = now
            if claim_limit > stream["stop_time"]:
                claim_limit = stream["stop_time"]

            last_claim = stream["last_claim_time"]
            if claim_limit > last_claim:
                elapsed = claim_limit - last_claim
                accumulated = elapsed * stream["rate_per_second"]
                if accumulated > U128(0):
                    token = self.storage.get("token")
                    self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), contributor, accumulated])

            stream["stop_time"] = claim_limit
            stream["last_claim_time"] = claim_limit
            stream["status"] = StreamStatus.TERMINATED
            self.storage.set(("stream", contributor), stream)

            self.env.emit_event("stream_terminated", {
                "contributor": contributor,
                "terminated_at": claim_limit,
            })

        # 2. Revoke vesting
        vesting = self.storage.get(("vesting", contributor), None)
        if vesting is not None and not vesting["revoked"]:
            now = self.env.ledger().timestamp()
            # Beneficiary receives whatever is vested up to now, the rest is forfeited
            claimable = U128(0)
            if now >= vesting["cliff_time"]:
                claimable = self._calculate_vested_amount(vesting, now)

            if claimable > U128(0):
                vesting["claimed_amount"] = vesting["claimed_amount"] + claimable
                token = self.storage.get("token")
                self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), contributor, claimable])

            vesting["revoked"] = True
            # Forfeit remainder
            forfeited = vesting["total_amount"] - vesting["claimed_amount"]
            self.storage.set(("vesting", contributor), vesting)

            self.env.emit_event("vesting_revoked", {
                "beneficiary": contributor,
                "forfeited_amount": forfeited,
            })

    @view
    def get_stream(self, contributor: Address) -> Map:
        """Get stream status and details."""
        return self.storage.get(("stream", contributor), {})

    @view
    def get_vesting(self, beneficiary: Address) -> Map:
        """Get vesting status and details."""
        return self.storage.get(("vesting", beneficiary), {})

    @view
    def get_review(self, contributor: Address, review_index: U64) -> Map:
        """Get a specific performance review."""
        return self.storage.get(("review", contributor, review_index), {})

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        caller.require_auth()
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _calculate_vested_amount(self, vesting: Map, now: U64) -> U128:
        if now < vesting["cliff_time"]:
            return U128(0)

        if now >= vesting["end_time"]:
            return vesting["total_amount"] - vesting["claimed_amount"]

        total_duration = vesting["end_time"] - vesting["start_time"]
        elapsed = now - vesting["start_time"]

        # vested_fraction = elapsed / total_duration
        vested = (elapsed * vesting["total_amount"]) / total_duration
        return vested - vesting["claimed_amount"]
