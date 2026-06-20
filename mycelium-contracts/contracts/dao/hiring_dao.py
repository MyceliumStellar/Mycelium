"""
Hiring DAO — Candidate nominations, multi-round interview voting, compensation approvals, and probation reviews.

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
    CANDIDATE_NOT_FOUND = 4
    INVALID_ROUND = 5
    VOTING_ACTIVE = 6
    VOTING_ENDED = 7
    ALREADY_VOTED = 8
    PROBATION_NOT_ENDED = 9
    INVALID_STATE = 10
    ZERO_VOTING_POWER = 11


class CandidateStatus:
    NOMINATED = 0
    SCREEN_VOTING = 1
    TECH_VOTING = 2
    COMP_VOTING = 3
    PROBATION = 4
    PROBATION_VOTING = 5
    HIRED = 6
    REJECTED = 7
    TERMINATED = 8


@contract
class HiringDAO:
    """A DAO contract designed to manage structured hiring pipelines, package approvals, and probation reviews."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        deposit_token: Address,
        voting_duration: U64,
        quorum_bps: U64,
    ):
        """Initialize the Hiring DAO contract.

        Args:
            admin: Admin address who manages operational items.
            deposit_token: Governance token used to measure voting power.
            voting_duration: Standard voting period for each round.
            quorum_bps: Quorum in basis points.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("token", deposit_token)
        self.storage.set("voting_duration", voting_duration)
        self.storage.set("quorum_bps", quorum_bps)

        self.storage.set("candidate_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "token": deposit_token,
            "voting_duration": voting_duration,
        })

    @external
    def nominate_candidate(
        self,
        nominator: Address,
        candidate_address: Address,
        role: Symbol,
        salary: U128,
        probation_duration: U64,
    ) -> U64:
        """Nominate a candidate for a role. Nominees start in NOMINATED status.

        Args:
            nominator: The DAO member nominating.
            candidate_address: Address of the candidate.
            role: Symbol detailing the role.
            salary: Proposed compensation package.
            probation_duration: Duration of probation period in seconds.
        """
        self._require_initialized()
        nominator.require_auth()

        # Nominator must have voting token power
        power = self._get_voting_power(nominator)
        if power == U128(0):
            raise ContractError.UNAUTHORIZED

        candidate_id = self.storage.get("candidate_count") + U64(1)
        self.storage.set("candidate_count", candidate_id)

        candidate = {
            "id": candidate_id,
            "candidate_address": candidate_address,
            "role": role,
            "salary": salary,
            "probation_duration": probation_duration,
            "status": CandidateStatus.NOMINATED,
            "hired_at": U64(0),
            "current_vote_end": U64(0),
        }

        self.storage.set(("candidate", candidate_id), candidate)

        self.env.emit_event("candidate_nominated", {
            "candidate_id": candidate_id,
            "candidate_address": candidate_address,
            "role": role,
            "nominator": nominator,
        })

        return candidate_id

    @external
    def start_next_round(self, caller: Address, candidate_id: U64):
        """Initiate the voting round for the candidate's current step in the pipeline.

        Args:
            caller: Any member or admin.
            candidate_id: ID of the candidate.
        """
        self._require_initialized()
        caller.require_auth()

        candidate = self._get_candidate(candidate_id)
        current_status = candidate["status"]

        next_status = CandidateStatus.REJECTED

        if current_status == CandidateStatus.NOMINATED:
            next_status = CandidateStatus.SCREEN_VOTING
        elif current_status == CandidateStatus.SCREEN_VOTING:
            # Check if previous screen voting ended and succeeded
            self._evaluate_round_result(candidate_id, CandidateStatus.SCREEN_VOTING)
            next_status = CandidateStatus.TECH_VOTING
        elif current_status == CandidateStatus.TECH_VOTING:
            self._evaluate_round_result(candidate_id, CandidateStatus.TECH_VOTING)
            next_status = CandidateStatus.COMP_VOTING
        elif current_status == CandidateStatus.COMP_VOTING:
            self._evaluate_round_result(candidate_id, CandidateStatus.COMP_VOTING)
            # If comp voting passes, they are hired under probation
            candidate["status"] = CandidateStatus.PROBATION
            candidate["hired_at"] = self.env.ledger().timestamp()
            self.storage.set(("candidate", candidate_id), candidate)

            self.env.emit_event("candidate_hired_on_probation", {
                "candidate_id": candidate_id,
                "hired_at": candidate["hired_at"],
            })
            return
        elif current_status == CandidateStatus.PROBATION:
            # Check if probation time has passed
            now = self.env.ledger().timestamp()
            if now < candidate["hired_at"] + candidate["probation_duration"]:
                raise ContractError.PROBATION_NOT_ENDED
            next_status = CandidateStatus.PROBATION_VOTING
        elif current_status == CandidateStatus.PROBATION_VOTING:
            self._evaluate_round_result(candidate_id, CandidateStatus.PROBATION_VOTING)
            candidate["status"] = CandidateStatus.HIRED
            self.storage.set(("candidate", candidate_id), candidate)

            self.env.emit_event("candidate_finalized", {
                "candidate_id": candidate_id,
                "status": CandidateStatus.HIRED,
            })
            return
        else:
            raise ContractError.INVALID_STATE

        # Prepare voting parameters for new active voting round
        now = self.env.ledger().timestamp()
        vote_end = now + self.storage.get("voting_duration")

        candidate["status"] = next_status
        candidate["current_vote_end"] = vote_end
        self.storage.set(("candidate", candidate_id), candidate)

        # Clear/initialize votes for this status
        self.storage.set(("round_votes", candidate_id, next_status), {
            "votes_for": U128(0),
            "votes_against": U128(0),
        })

        self.env.emit_event("round_started", {
            "candidate_id": candidate_id,
            "round_status": next_status,
            "vote_end": vote_end,
        })

    @external
    def cast_vote(self, voter: Address, candidate_id: U64, vote_type: U64):
        """Cast vote on the active round for the candidate.

        Args:
            voter: DAO member address.
            candidate_id: Candidate ID.
            vote_type: 0 for AGAINST, 1 for FOR.
        """
        self._require_initialized()
        voter.require_auth()

        candidate = self._get_candidate(candidate_id)
        current_status = candidate["status"]

        # Only allowed if candidate is in a voting stage
        if (current_status != CandidateStatus.SCREEN_VOTING and
            current_status != CandidateStatus.TECH_VOTING and
            current_status != CandidateStatus.COMP_VOTING and
            current_status != CandidateStatus.PROBATION_VOTING):
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        if now >= candidate["current_vote_end"]:
            raise ContractError.VOTING_ENDED

        already_voted = self.storage.get(("voted", candidate_id, current_status, voter), False)
        if already_voted:
            raise ContractError.ALREADY_VOTED

        voting_power = self._get_voting_power(voter)
        if voting_power == U128(0):
            raise ContractError.ZERO_VOTING_POWER

        votes = self.storage.get(("round_votes", candidate_id, current_status))
        if vote_type == U64(1):
            votes["votes_for"] = votes["votes_for"] + voting_power
        elif vote_type == U64(0):
            votes["votes_against"] = votes["votes_against"] + voting_power
        else:
            raise ContractError.INVALID_STATE

        self.storage.set(("round_votes", candidate_id, current_status), votes)
        self.storage.set(("voted", candidate_id, current_status, voter), True)

        self.env.emit_event("vote_cast", {
            "candidate_id": candidate_id,
            "round": current_status,
            "voter": voter,
            "vote_type": vote_type,
            "weight": voting_power,
        })

    @view
    def get_candidate(self, candidate_id: U64) -> Map:
        """Get candidate pipeline details."""
        return self._get_candidate(candidate_id)

    @view
    def get_round_votes(self, candidate_id: U64, round_status: U64) -> Map:
        """Get votes cast in a specific pipeline round."""
        votes = self.storage.get(("round_votes", candidate_id, round_status), None)
        if votes is None:
            return {"votes_for": U128(0), "votes_against": U128(0)}
        return votes

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _get_candidate(self, candidate_id: U64) -> Map:
        candidate = self.storage.get(("candidate", candidate_id), None)
        if candidate is None:
            raise ContractError.CANDIDATE_NOT_FOUND
        return candidate

    def _evaluate_round_result(self, candidate_id: U64, round_status: U64):
        candidate = self._get_candidate(candidate_id)
        now = self.env.ledger().timestamp()
        if now < candidate["current_vote_end"]:
            raise ContractError.VOTING_ACTIVE

        votes = self.storage.get(("round_votes", candidate_id, round_status))
        total_supply = self._get_total_supply()
        quorum_bps = self.storage.get("quorum_bps")
        required_quorum = (total_supply * U128(quorum_bps)) / U128(10000)

        total_votes = votes["votes_for"] + votes["votes_against"]
        passed = (total_votes >= required_quorum) and (votes["votes_for"] > votes["votes_against"])

        if not passed:
            # Candidate rejected and locked out
            if round_status == CandidateStatus.PROBATION_VOTING:
                candidate["status"] = CandidateStatus.TERMINATED
            else:
                candidate["status"] = CandidateStatus.REJECTED

            self.storage.set(("candidate", candidate_id), candidate)
            self.env.emit_event("candidate_rejected", {
                "candidate_id": candidate_id,
                "failed_round": round_status,
            })
            raise ContractError.INVALID_STATE

    def _get_voting_power(self, voter: Address) -> U128:
        token = self.storage.get("token")
        return self.env.invoke_contract(token, "balance", [voter])

    def _get_total_supply(self) -> U128:
        token = self.storage.get("token")
        return self.env.invoke_contract(token, "total_supply", [])
