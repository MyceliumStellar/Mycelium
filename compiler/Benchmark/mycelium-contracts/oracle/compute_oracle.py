"""
Compute Oracle — Off-chain computing registry with input/output validation, disputes, and gas rebates.

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
    INSUFFICIENT_BOUNTY = 4
    JOB_NOT_FOUND = 5
    INVALID_STATE = 6
    TIMEOUT_NOT_EXCEEDED = 7
    TRANSFER_FAILED = 8
    GAS_LIMIT_EXCEEDED = 9
    DISPUTE_PERIOD_EXPIRED = 10
    ALREADY_VOTED = 11
    JURY_VOTING_ACTIVE = 12
    JURY_VOTING_ENDED = 13
    REENTRANT_CALL = 14


class JobStatus:
    PENDING = 0
    SUBMITTED = 1
    DISPUTED = 2
    FINALIZED = 3
    REFUNDED = 4


@contract
class ComputeOracle:
    """Compute Oracle contract facilitating off-chain job execution requests,
    verifying provider outcomes, hosting a jury dispute mechanism, and dispensing gas rebates."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        token: Address,
        gas_price: U128,
        dispute_period: U64,
        timeout: U64,
    ):
        """Initialize Configurations."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("token", token)
        self.storage.set("gas_price", gas_price)
        self.storage.set("dispute_period", dispute_period)
        self.storage.set("timeout", timeout)
        self.storage.set("job_count", U64(0))
        
        self.storage.set("execution_lock", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "token": token,
            "gas_price": gas_price,
            "dispute_period": dispute_period,
        })

    # ------------------------------------------------------------------ #
    #  Job Requests                                                       #
    # ------------------------------------------------------------------ #

    @external
    def request_job(
        self,
        caller: Address,
        ipfs_input_hash: Bytes,
        provider: Address,
        max_gas: U64,
        bounty: U128,
    ) -> U64:
        """Submit an off-chain compute job request.

        Args:
            caller: Client requesting the job.
            ipfs_input_hash: IPFS hash of job inputs.
            provider: Target provider to execute.
            max_gas: Maximum gas allowed.
            bounty: Token payment locked for execution.
        """
        self._require_initialized()
        caller.require_auth()

        token = self.storage.get("token")
        contract_addr = self.env.current_contract_address()
        
        # Pull bounty tokens
        success = self.env.invoke_contract(token, "transfer", [caller, contract_addr, bounty])
        if not success:
            raise ContractError.TRANSFER_FAILED

        job_id = self.storage.get("job_count") + U64(1)
        self.storage.set("job_count", job_id)

        now = self.env.ledger().timestamp()

        job = {
            "id": job_id,
            "client": caller,
            "ipfs_input_hash": ipfs_input_hash,
            "provider": provider,
            "max_gas": max_gas,
            "bounty": bounty,
            "status": JobStatus.PENDING,
            "created_at": now,
            "ipfs_output_hash": Bytes(b""),
            "gas_used": U64(0),
            "dispute_deadline": U64(0),
            "disputer": caller, # placeholder
            "jury_votes_valid": U64(0),
            "jury_votes_invalid": U64(0),
            "vote_deadline": U64(0),
        }

        self.storage.set(("job", job_id), job)

        self.env.emit_event("job_requested", {
            "job_id": job_id,
            "client": caller,
            "provider": provider,
            "bounty": bounty,
        })

        return job_id

    # ------------------------------------------------------------------ #
    #  Fulfillment                                                        #
    # ------------------------------------------------------------------ #

    @external
    def submit_result(
        self,
        provider: Address,
        job_id: U64,
        ipfs_output_hash: Bytes,
        gas_used: U64,
    ):
        """Submit the computation output. Only target provider.

        Args:
            provider: Authorized provider.
            job_id: The job ID.
            ipfs_output_hash: Compute output data IPFS hash.
            gas_used: Gas amount consumed.
        """
        self._require_initialized()
        provider.require_auth()

        job = self.storage.get(("job", job_id), None)
        if job is None:
            raise ContractError.JOB_NOT_FOUND

        if job["provider"] != provider:
            raise ContractError.UNAUTHORIZED
        if job["status"] != JobStatus.PENDING:
            raise ContractError.INVALID_STATE

        if gas_used > job["max_gas"]:
            raise ContractError.GAS_LIMIT_EXCEEDED

        now = self.env.ledger().timestamp()
        dispute_period = self.storage.get("dispute_period")

        job["ipfs_output_hash"] = ipfs_output_hash
        job["gas_used"] = gas_used
        job["status"] = JobStatus.SUBMITTED
        job["dispute_deadline"] = now + dispute_period

        self.storage.set(("job", job_id), job)

        self.env.emit_event("job_submitted", {
            "job_id": job_id,
            "provider": provider,
            "dispute_deadline": job["dispute_deadline"],
        })

    # ------------------------------------------------------------------ #
    #  Disputes & Jury Voting                                             #
    # ------------------------------------------------------------------ #

    @external
    def dispute_result(self, disputer: Address, job_id: U64):
        """Dispute a submitted computation result.

        Args:
            disputer: Address of the challenger.
            job_id: Job ID being contested.
        """
        self._require_initialized()
        disputer.require_auth()

        job = self.storage.get(("job", job_id), None)
        if job is None:
            raise ContractError.JOB_NOT_FOUND

        if job["status"] != JobStatus.SUBMITTED:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        if now > job["dispute_deadline"]:
            raise ContractError.DISPUTE_PERIOD_EXPIRED

        # Lock a dispute stake (e.g. same as gas price or configuration)
        stake_amount = job["bounty"] / U128(2) # 50% of bounty required to challenge
        token = self.storage.get("token")
        contract_addr = self.env.current_contract_address()
        success = self.env.invoke_contract(token, "transfer", [disputer, contract_addr, stake_amount])
        if not success:
            raise ContractError.TRANSFER_FAILED

        job["status"] = JobStatus.DISPUTED
        job["disputer"] = disputer
        
        # Convene jury: voting period is same as dispute period
        dispute_period = self.storage.get("dispute_period")
        job["vote_deadline"] = now + dispute_period

        self.storage.set(("job", job_id), job)

        self.env.emit_event("job_disputed", {
            "job_id": job_id,
            "disputer": disputer,
            "vote_deadline": job["vote_deadline"],
        })

    @external
    def cast_jury_vote(self, jury_member: Address, job_id: U64, is_valid: Bool):
        """Cast vote on disputed job. Only registered Jury members.

        Args:
            jury_member: Staked/authorized jury member.
            job_id: Contested job ID.
            is_valid: True to support provider's result, False to veto.
        """
        self._require_initialized()
        jury_member.require_auth()
        self._require_jury_member(jury_member)

        job = self.storage.get(("job", job_id), None)
        if job is None:
            raise ContractError.JOB_NOT_FOUND

        if job["status"] != JobStatus.DISPUTED:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        if now > job["vote_deadline"]:
            raise ContractError.JURY_VOTING_ENDED

        already_voted = self.storage.get(("jury_voted", job_id, jury_member), False)
        if already_voted:
            raise ContractError.ALREADY_VOTED

        if is_valid:
            job["jury_votes_valid"] = job["jury_votes_valid"] + U64(1)
        else:
            job["jury_votes_invalid"] = job["jury_votes_invalid"] + U64(1)

        self.storage.set(("jury_voted", job_id, jury_member), True)
        self.storage.set(("job", job_id), job)

        self.env.emit_event("jury_vote_cast", {
            "job_id": job_id,
            "voter": jury_member,
            "is_valid": is_valid,
        })

    @external
    def resolve_dispute(self, caller: Address, job_id: U64):
        """Resolve a dispute after the jury voting deadline has passed.

        Args:
            caller: Any address.
            job_id: The disputed job.
        """
        self._require_initialized()
        caller.require_auth()
        self._require_no_reentrant()

        job = self.storage.get(("job", job_id), None)
        if job is None:
            raise ContractError.JOB_NOT_FOUND

        if job["status"] != JobStatus.DISPUTED:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        if now <= job["vote_deadline"]:
            raise ContractError.JURY_VOTING_ACTIVE

        votes_valid = job["jury_votes_valid"]
        votes_invalid = job["jury_votes_invalid"]

        token = self.storage.get("token")
        contract_addr = self.env.current_contract_address()
        bounty = job["bounty"]
        dispute_stake = bounty / U128(2)

        if votes_valid > votes_invalid:
            # Provider result was correct!
            job["status"] = JobStatus.FINALIZED
            
            # Slashed dispute stake is split among voting jury members (if any), or goes to provider
            # Here: Operator payout
            gas_rebate = U128(job["gas_used"]) * self.storage.get("gas_price")
            payout = bounty
            if payout < gas_rebate:
                payout = gas_rebate

            self.env.invoke_contract(token, "transfer", [contract_addr, job["provider"], payout + dispute_stake])
        else:
            # Challenger was correct! Provider was wrong.
            job["status"] = JobStatus.REFUNDED
            
            # Refund client bounty
            self.env.invoke_contract(token, "transfer", [contract_addr, job["client"], bounty])
            # Refund challenger stake + bonus from provider stake (here just refund challenger stake)
            self.env.invoke_contract(token, "transfer", [contract_addr, job["disputer"], dispute_stake])

        self.storage.set(("job", job_id), job)

        self.env.emit_event("job_dispute_resolved", {
            "job_id": job_id,
            "votes_valid": votes_valid,
            "votes_invalid": votes_invalid,
        })

    # ------------------------------------------------------------------ #
    #  Finalization & Timeout                                             #
    # ------------------------------------------------------------------ #

    @external
    def finalize_job(self, caller: Address, job_id: U64):
        """Finalize job if the dispute period has passed. Pays provider.

        Args:
            caller: Any address.
            job_id: Job ID.
        """
        self._require_initialized()
        caller.require_auth()
        self._require_no_reentrant()

        job = self.storage.get(("job", job_id), None)
        if job is None:
            raise ContractError.JOB_NOT_FOUND

        if job["status"] != JobStatus.SUBMITTED:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        if now <= job["dispute_deadline"]:
            raise ContractError.DISPUTE_PERIOD_ACTIVE

        job["status"] = JobStatus.FINALIZED
        self.storage.set(("job", job_id), job)

        # Pay provider: gas rebate + base payout
        gas_rebate = U128(job["gas_used"]) * self.storage.get("gas_price")
        payout = job["bounty"]
        if payout < gas_rebate:
            payout = gas_rebate

        token = self.storage.get("token")
        contract_addr = self.env.current_contract_address()
        self.env.invoke_contract(token, "transfer", [contract_addr, job["provider"], payout])

        self.env.emit_event("job_finalized", {
            "job_id": job_id,
            "provider": job["provider"],
            "payout": payout,
        })

    @external
    def refund_timeout(self, client: Address, job_id: U64):
        """Refund bounty to client if provider has not submitted within timeout duration.

        Args:
            client: Job client creator.
            job_id: Job ID.
        """
        self._require_initialized()
        client.require_auth()
        self._require_no_reentrant()

        job = self.storage.get(("job", job_id), None)
        if job is None:
            raise ContractError.JOB_NOT_FOUND

        if job["client"] != client:
            raise ContractError.UNAUTHORIZED
        if job["status"] != JobStatus.PENDING:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        timeout = self.storage.get("timeout")
        if now <= job["created_at"] + timeout:
            raise ContractError.TIMEOUT_NOT_EXCEEDED

        job["status"] = JobStatus.REFUNDED
        self.storage.set(("job", job_id), job)

        token = self.storage.get("token")
        contract_addr = self.env.current_contract_address()
        self.env.invoke_contract(token, "transfer", [contract_addr, client, job["bounty"]])

        self.env.emit_event("job_refunded", {
            "job_id": job_id,
            "client": client,
        })

    # ------------------------------------------------------------------ #
    #  Admin Configurations                                               #
    # ------------------------------------------------------------------ #

    @external
    def set_jury_member(self, admin: Address, juror: Address, status: Bool):
        """Add or remove a jury member. Only Admin."""
        self._require_admin(admin)
        self.storage.set(("jury", juror), status)
        self.env.emit_event("jury_updated", {"juror": juror, "status": status})

    @external
    def update_config(self, admin: Address, gas_price: U128, dispute_period: U64, timeout: U64):
        """Update configurations. Only Admin."""
        self._require_admin(admin)
        self.storage.set("gas_price", gas_price)
        self.storage.set("dispute_period", dispute_period)
        self.storage.set("timeout", timeout)
        self.env.emit_event("config_updated", {"gas_price": gas_price, "dispute_period": dispute_period})

    @external
    def transfer_admin(self, admin: Address, new_admin: Address):
        """Transfer admin role. Only Admin."""
        self._require_admin(admin)
        self.storage.set("admin", new_admin)
        self.env.emit_event("admin_transferred", {"old_admin": admin, "new_admin": new_admin})

    # ------------------------------------------------------------------ #
    #  View Functions                                                     #
    # ------------------------------------------------------------------ #

    @view
    def get_job(self, job_id: U64) -> Map:
        """Get job execution registry details."""
        self._require_initialized()
        job = self.storage.get(("job", job_id), None)
        if job is None:
            raise ContractError.JOB_NOT_FOUND
        return job

    @view
    def is_jury_member(self, juror: Address) -> Bool:
        """Check if juror is authorized."""
        self._require_initialized()
        return self.storage.get(("jury", juror), False)

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                   #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        caller.require_auth()
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_jury_member(self, caller: Address):
        if not self.storage.get(("jury", caller), False):
            raise ContractError.UNAUTHORIZED

    def _require_no_reentrant(self):
        if self.storage.get("execution_lock", False):
            raise ContractError.REENTRANT_CALL
