"""
Grants DAO — Application reviews, milestone-based releases, grantee reports, milestone voting, and clawbacks.

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
    GRANT_NOT_FOUND = 4
    INVALID_MILESTONE = 5
    INVALID_STATE = 6
    ALREADY_VOTED = 7
    INSUFFICIENT_FUNDS = 8
    VOTING_ACTIVE = 9
    VOTING_ENDED = 10
    INVALID_MILESTONE_PAYOUTS = 11


class GrantStatus:
    PROPOSED = 0
    ACTIVE = 1
    COMPLETED = 2
    CLAWED_BACK = 3
    DEFEATED = 4


class MilestoneStatus:
    LOCKED = 0
    SUBMITTED = 1
    APPROVED = 2
    REJECTED = 3


@contract
class GrantsDAO:
    """A DAO contract focused on managing grant applications, milestone delivery reviews, and clawbacks."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        deposit_token: Address,
        quorum_bps: U64,
        review_duration: U64,
    ):
        """Initialize the Grants DAO.

        Args:
            admin: Contract admin who configures DAO parameters.
            deposit_token: Token used for grant payouts.
            quorum_bps: Minimum vote quorum in basis points (100 = 1%).
            review_duration: Standard voting period for applications and milestones.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("token", deposit_token)
        self.storage.set("quorum_bps", quorum_bps)
        self.storage.set("review_duration", review_duration)
        self.storage.set("grant_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "token": deposit_token,
            "quorum_bps": quorum_bps,
        })

    @external
    def submit_grant_application(
        self,
        grantee: Address,
        total_amount: U128,
        milestone_payouts: Vec,
        description: Symbol,
    ) -> U64:
        """Apply for a new grant with a specific set of milestone payouts.

        Args:
            grantee: Address receiving the payouts.
            total_amount: Sum of all milestone payouts.
            milestone_payouts: Vec of U128 payouts for each milestone.
            description: Description symbol of the grant proposal.
        """
        self._require_initialized()
        grantee.require_auth()

        # Payouts validation
        total_payout_sum = U128(0)
        for payout in milestone_payouts:
            total_payout_sum = total_payout_sum + payout

        if total_payout_sum != total_amount or len(milestone_payouts) == 0:
            raise ContractError.INVALID_MILESTONE_PAYOUTS

        grant_id = self.storage.get("grant_count") + U64(1)
        self.storage.set("grant_count", grant_id)

        now = self.env.ledger().timestamp()
        vote_end = now + self.storage.get("review_duration")

        # Save milestone payouts
        for i in range(len(milestone_payouts)):
            payout = milestone_payouts[i]
            # milestone_index is 0-indexed internally
            self.storage.set(("milestone_payout", grant_id, U64(i)), payout)
            self.storage.set(("milestone_status", grant_id, U64(i)), MilestoneStatus.LOCKED)

        grant = {
            "id": grant_id,
            "grantee": grantee,
            "total_amount": total_amount,
            "total_milestones": U64(len(milestone_payouts)),
            "current_milestone": U64(0), # 0 indicates proposal phase, 1 is first milestone
            "status": GrantStatus.PROPOSED,
            "vote_end": vote_end,
            "votes_for": U128(0),
            "votes_against": U128(0),
        }

        self.storage.set(("grant", grant_id), grant)

        self.env.emit_event("grant_application_submitted", {
            "grant_id": grant_id,
            "grantee": grantee,
            "total_amount": total_amount,
            "milestone_count": U64(len(milestone_payouts)),
        })

        return grant_id

    @external
    def cast_application_vote(self, voter: Address, grant_id: U64, vote_type: U64):
        """Cast vote on initial grant approval using voter's token balance weight.

        Args:
            voter: DAO voter address.
            grant_id: Grant ID.
            vote_type: 0 for AGAINST, 1 for FOR.
        """
        self._require_initialized()
        voter.require_auth()

        grant = self._get_grant(grant_id)
        if grant["status"] != GrantStatus.PROPOSED:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        if now >= grant["vote_end"]:
            raise ContractError.VOTING_ENDED

        already_voted = self.storage.get(("voted_application", grant_id, voter), False)
        if already_voted:
            raise ContractError.ALREADY_VOTED

        voting_power = self._get_voting_power(voter)
        if voting_power == U128(0):
            raise ContractError.UNAUTHORIZED

        if vote_type == U64(1):
            grant["votes_for"] = grant["votes_for"] + voting_power
        elif vote_type == U64(0):
            grant["votes_against"] = grant["votes_against"] + voting_power
        else:
            raise ContractError.INVALID_STATE

        self.storage.set(("grant", grant_id), grant)
        self.storage.set(("voted_application", grant_id, voter), True)

        self.env.emit_event("application_vote_cast", {
            "grant_id": grant_id,
            "voter": voter,
            "vote_type": vote_type,
            "weight": voting_power,
        })

    @external
    def finalize_application(self, caller: Address, grant_id: U64):
        """Finalize initial grant application review. Anyone can trigger.

        Args:
            caller: Trigger address.
            grant_id: Grant ID.
        """
        self._require_initialized()
        caller.require_auth()

        grant = self._get_grant(grant_id)
        if grant["status"] != GrantStatus.PROPOSED:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        if now < grant["vote_end"]:
            raise ContractError.VOTING_ACTIVE

        total_supply = self._get_total_supply()
        quorum_bps = self.storage.get("quorum_bps")
        required_quorum = (total_supply * U128(quorum_bps)) / U128(10000)

        total_votes = grant["votes_for"] + grant["votes_against"]
        passed = (total_votes >= required_quorum) and (grant["votes_for"] > grant["votes_against"])

        if passed:
            grant["status"] = GrantStatus.ACTIVE
            grant["current_milestone"] = U64(0)  # Ready for first milestone submission (milestone index 0)
        else:
            grant["status"] = GrantStatus.DEFEATED

        self.storage.set(("grant", grant_id), grant)

        self.env.emit_event("grant_application_finalized", {
            "grant_id": grant_id,
            "status": grant["status"],
        })

    @external
    def submit_milestone_completion(self, grantee: Address, grant_id: U64, milestone_index: U64, report_link: Symbol):
        """Grantee submits report for completed milestone, initiating review.

        Args:
            grantee: Must be the grantee.
            grant_id: Grant ID.
            milestone_index: Milestone index to complete (0-indexed).
            report_link: Report URL/details.
        """
        self._require_initialized()
        grantee.require_auth()

        grant = self._get_grant(grant_id)
        if grant["status"] != GrantStatus.ACTIVE:
            raise ContractError.INVALID_STATE
        if grantee != grant["grantee"]:
            raise ContractError.UNAUTHORIZED
        if milestone_index != grant["current_milestone"]:
            raise ContractError.INVALID_MILESTONE

        m_status = self.storage.get(("milestone_status", grant_id, milestone_index))
        if m_status != MilestoneStatus.LOCKED and m_status != MilestoneStatus.REJECTED:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        vote_end = now + self.storage.get("review_duration")

        self.storage.set(("milestone_status", grant_id, milestone_index), MilestoneStatus.SUBMITTED)
        self.storage.set(("milestone_vote_end", grant_id, milestone_index), vote_end)
        self.storage.set(("milestone_votes_for", grant_id, milestone_index), U128(0))
        self.storage.set(("milestone_votes_against", grant_id, milestone_index), U128(0))

        self.env.emit_event("milestone_submitted", {
            "grant_id": grant_id,
            "milestone_index": milestone_index,
            "report_link": report_link,
            "vote_end": vote_end,
        })

    @external
    def cast_milestone_vote(self, voter: Address, grant_id: U64, milestone_index: U64, vote_type: U64):
        """Vote on whether the milestone was completed.

        Args:
            voter: DAO voter.
            grant_id: Grant ID.
            milestone_index: Index of milestone being voted on.
            vote_type: 0 for AGAINST, 1 for FOR.
        """
        self._require_initialized()
        voter.require_auth()

        grant = self._get_grant(grant_id)
        if grant["status"] != GrantStatus.ACTIVE:
            raise ContractError.INVALID_STATE

        m_status = self.storage.get(("milestone_status", grant_id, milestone_index))
        if m_status != MilestoneStatus.SUBMITTED:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        vote_end = self.storage.get(("milestone_vote_end", grant_id, milestone_index))
        if now >= vote_end:
            raise ContractError.VOTING_ENDED

        already_voted = self.storage.get(("voted_milestone", grant_id, milestone_index, voter), False)
        if already_voted:
            raise ContractError.ALREADY_VOTED

        voting_power = self._get_voting_power(voter)
        if voting_power == U128(0):
            raise ContractError.UNAUTHORIZED

        v_for = self.storage.get(("milestone_votes_for", grant_id, milestone_index))
        v_against = self.storage.get(("milestone_votes_against", grant_id, milestone_index))

        if vote_type == U64(1):
            self.storage.set(("milestone_votes_for", grant_id, milestone_index), v_for + voting_power)
        elif vote_type == U64(0):
            self.storage.set(("milestone_votes_against", grant_id, milestone_index), v_against + voting_power)
        else:
            raise ContractError.INVALID_STATE

        self.storage.set(("voted_milestone", grant_id, milestone_index, voter), True)

        self.env.emit_event("milestone_vote_cast", {
            "grant_id": grant_id,
            "milestone_index": milestone_index,
            "voter": voter,
            "vote_type": vote_type,
            "weight": voting_power,
        })

    @external
    def finalize_milestone(self, caller: Address, grant_id: U64, milestone_index: U64):
        """Finalize the milestone review vote. Releasess payout if successful.

        Args:
            caller: Call initiator.
            grant_id: Grant ID.
            milestone_index: Milestone index.
        """
        self._require_initialized()
        caller.require_auth()

        grant = self._get_grant(grant_id)
        if grant["status"] != GrantStatus.ACTIVE:
            raise ContractError.INVALID_STATE
        if milestone_index != grant["current_milestone"]:
            raise ContractError.INVALID_MILESTONE

        m_status = self.storage.get(("milestone_status", grant_id, milestone_index))
        if m_status != MilestoneStatus.SUBMITTED:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        vote_end = self.storage.get(("milestone_vote_end", grant_id, milestone_index))
        if now < vote_end:
            raise ContractError.VOTING_ACTIVE

        total_supply = self._get_total_supply()
        quorum_bps = self.storage.get("quorum_bps")
        required_quorum = (total_supply * U128(quorum_bps)) / U128(10000)

        v_for = self.storage.get(("milestone_votes_for", grant_id, milestone_index))
        v_against = self.storage.get(("milestone_votes_against", grant_id, milestone_index))
        total_votes = v_for + v_against

        passed = (total_votes >= required_quorum) and (v_for > v_against)

        if passed:
            self.storage.set(("milestone_status", grant_id, milestone_index), MilestoneStatus.APPROVED)
            payout_amount = self.storage.get(("milestone_payout", grant_id, milestone_index))

            # Send grant funds to grantee
            token = self.storage.get("token")
            success = self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), grant["grantee"], payout_amount])
            if not success:
                raise ContractError.INSUFFICIENT_FUNDS

            # Advance milestone counter
            next_m = milestone_index + U64(1)
            grant["current_milestone"] = next_m

            # Check if all milestones completed
            if next_m >= grant["total_milestones"]:
                grant["status"] = GrantStatus.COMPLETED

            self.storage.set(("grant", grant_id), grant)

            self.env.emit_event("milestone_approved", {
                "grant_id": grant_id,
                "milestone_index": milestone_index,
                "payout": payout_amount,
            })
        else:
            # Milestone rejected, grantee can re-submit after fixing or DAO can choose to clawback
            self.storage.set(("milestone_status", grant_id, milestone_index), MilestoneStatus.REJECTED)
            self.env.emit_event("milestone_rejected", {
                "grant_id": grant_id,
                "milestone_index": milestone_index,
            })

    @external
    def clawback(self, admin: Address, grant_id: U64):
        """Cancel the remaining grant budget on project failure. Only admin.

        Args:
            admin: Admin address.
            grant_id: Grant ID.
        """
        self._require_initialized()
        admin.require_auth()

        registered_admin = self.storage.get("admin")
        if admin != registered_admin:
            raise ContractError.UNAUTHORIZED

        grant = self._get_grant(grant_id)
        if grant["status"] != GrantStatus.ACTIVE:
            raise ContractError.INVALID_STATE

        # Change grant status to ClawedBack
        grant["status"] = GrantStatus.CLAWED_BACK
        self.storage.set(("grant", grant_id), grant)

        self.env.emit_event("grant_clawed_back", {
            "grant_id": grant_id,
            "refunded_milestone": grant["current_milestone"],
        })

    @view
    def get_grant(self, grant_id: U64) -> Map:
        """Get details of a grant application."""
        return self._get_grant(grant_id)

    @view
    def get_milestone(self, grant_id: U64, milestone_index: U64) -> Map:
        """Get status and payouts of a milestone."""
        return {
            "payout": self.storage.get(("milestone_payout", grant_id, milestone_index), U128(0)),
            "status": self.storage.get(("milestone_status", grant_id, milestone_index), MilestoneStatus.LOCKED),
            "vote_end": self.storage.get(("milestone_vote_end", grant_id, milestone_index), U64(0)),
            "votes_for": self.storage.get(("milestone_votes_for", grant_id, milestone_index), U128(0)),
            "votes_against": self.storage.get(("milestone_votes_against", grant_id, milestone_index), U128(0)),
        }

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _get_grant(self, grant_id: U64) -> Map:
        grant = self.storage.get(("grant", grant_id), None)
        if grant is None:
            raise ContractError.GRANT_NOT_FOUND
        return grant

    def _get_voting_power(self, voter: Address) -> U128:
        token = self.storage.get("token")
        return self.env.invoke_contract(token, "balance", [voter])

    def _get_total_supply(self) -> U128:
        token = self.storage.get("token")
        return self.env.invoke_contract(token, "total_supply", [])
