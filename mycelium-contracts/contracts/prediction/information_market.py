"""
Information Market — Information verification bounties, accuracy scoring, reporter ratings, payout splits.

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
    BOUNTY_RESOLVED = 4
    BOUNTY_NOT_RESOLVED = 5
    BOUNTY_NOT_FOUND = 6
    REPORT_ALREADY_SUBMITTED = 7
    INSUFFICIENT_BALANCE = 8
    ZERO_AMOUNT = 9
    NO_CORRECT_REPORTERS = 10


class BountyState:
    ACTIVE = 0
    RESOLVED = 1


@contract
class InformationMarket:
    """A contract for managing information verification bounties with reporter rating calculations."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, collateral_token: Address):
        """Initialize the information market registry.

        Args:
            admin: Admin address.
            collateral_token: Backing token for bounties.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("token", collateral_token)
        self.storage.set("bounty_counter", U64(0))

        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "token": collateral_token,
        })

    @external
    def create_bounty(
        self,
        creator: Address,
        description_hash: Bytes,
        reward_amount: U128,
        submission_deadline: U64,
    ) -> U64:
        """Create a new information verification bounty.

        Args:
            creator: Bounty sponsor.
            description_hash: Cryptographic hash of details.
            reward_amount: Bounty payout amount.
            submission_deadline: Deadline for reporters to submit reports.
        """
        self._require_initialized()
        creator.require_auth()

        if reward_amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        token = self.storage.get("token")
        success = self.env.invoke_contract(token, "transfer", [creator, self.env.current_contract_address(), reward_amount])
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        bounty_id = self.storage.get("bounty_counter") + U64(1)
        self.storage.set("bounty_counter", bounty_id)

        bounty = Map()
        bounty.set("id", bounty_id)
        bounty.set("creator", creator)
        bounty.set("description_hash", description_hash)
        bounty.set("reward_amount", reward_amount)
        bounty.set("deadline", submission_deadline)
        bounty.set("state", BountyState.ACTIVE)
        bounty.set("true_outcome", Symbol(""))
        bounty.set("reporters", Vec()) # Vector of reporters who submitted reports
        bounty.set("reporters_count", U64(0))

        self.storage.set(("bounty", bounty_id), bounty)

        self.env.emit_event("bounty_created", {
            "bounty_id": bounty_id,
            "creator": creator,
            "reward": reward_amount,
        })

        return bounty_id

    @external
    def submit_report(self, reporter: Address, bounty_id: U64, outcome: Symbol, evidence_hash: Bytes):
        """Submit a verification report with evidence.

        Args:
            reporter: Reporter.
            bounty_id: Target bounty.
            outcome: Claimed outcome.
            evidence_hash: Link to verification files/metadata.
        """
        self._require_initialized()
        reporter.require_auth()

        bounty = self.storage.get(("bounty", bounty_id), None)
        if bounty is None:
            raise ContractError.BOUNTY_NOT_FOUND

        if bounty.get("state") != BountyState.ACTIVE:
            raise ContractError.BOUNTY_RESOLVED

        now = self.env.ledger().timestamp()
        if now >= bounty.get("deadline"):
            raise ContractError.BOUNTY_RESOLVED

        # Check if already submitted
        has_submitted = self.storage.get(("submitted", bounty_id, reporter), False)
        if has_submitted:
            raise ContractError.REPORT_ALREADY_SUBMITTED

        # Get reporter current rating (default: 1000)
        rating = self.storage.get(("reporter_rating", reporter), U64(1000))

        report = Map()
        report.set("reporter", reporter)
        report.set("outcome", outcome)
        report.set("evidence_hash", evidence_hash)
        report.set("rating_at_submission", rating)
        report.set("timestamp", now)

        self.storage.set(("report", bounty_id, reporter), report)
        self.storage.set(("submitted", bounty_id, reporter), True)

        reporters = bounty.get("reporters")
        reporters.append(reporter)
        bounty.set("reporters", reporters)
        bounty.set("reporters_count", bounty.get("reporters_count") + U64(1))
        self.storage.set(("bounty", bounty_id), bounty)

        self.env.emit_event("report_submitted", {
            "bounty_id": bounty_id,
            "reporter": reporter,
            "outcome": outcome,
        })

    @external
    def resolve_bounty(self, caller: Address, bounty_id: U64, true_outcome: Symbol):
        """Verify the correct outcome and update reporter ratings. Only admin.

        Args:
            caller: Admin.
            bounty_id: Target bounty.
            true_outcome: True verified outcome.
        """
        self._require_initialized()
        self._require_admin(caller)

        bounty = self.storage.get(("bounty", bounty_id), None)
        if bounty is None:
            raise ContractError.BOUNTY_NOT_FOUND

        if bounty.get("state") != BountyState.ACTIVE:
            raise ContractError.BOUNTY_RESOLVED

        bounty.set("true_outcome", true_outcome)
        bounty.set("state", BountyState.RESOLVED)
        self.storage.set(("bounty", bounty_id), bounty)

        reporters = bounty.get("reporters")
        num_reporters = len(reporters)

        # Update all reporter ratings
        for i in range(num_reporters):
            rep = reporters.get(i)
            rep_report = self.storage.get(("report", bounty_id, rep))
            current_rating = self.storage.get(("reporter_rating", rep), U64(1000))

            if rep_report.get("outcome") == true_outcome:
                # Correct: Rating increase (+50)
                new_rating = current_rating + U64(50)
                if new_rating > U64(2000):
                    new_rating = U64(2000)
                self.storage.set(("reporter_rating", rep), new_rating)
            else:
                # Incorrect: Rating decrease (-100)
                if current_rating > U64(100):
                    new_rating = current_rating - U64(100)
                else:
                    new_rating = U64(100)
                self.storage.set(("reporter_rating", rep), new_rating)

        self.env.emit_event("bounty_resolved", {
            "bounty_id": bounty_id,
            "true_outcome": true_outcome,
        })

    @external
    def claim_payout(self, claimant: Address, bounty_id: U64) -> U128:
        """Claim a reporter's proportional share of the bounty reward.

        Proportions are determined by:
          - (rating_at_submission * speed_multiplier)
          - Earlier reporters get speed_multiplier (first reporter gets 1.5x weight, others 1.0x).
        """
        self._require_initialized()
        claimant.require_auth()

        bounty = self.storage.get(("bounty", bounty_id), None)
        if bounty is None:
            raise ContractError.BOUNTY_NOT_FOUND

        if bounty.get("state") != BountyState.RESOLVED:
            raise ContractError.BOUNTY_NOT_RESOLVED

        # Check if claimant submitted a report
        has_submitted = self.storage.get(("submitted", bounty_id, claimant), False)
        if not has_submitted:
            raise ContractError.UNAUTHORIZED

        report = self.storage.get(("report", bounty_id, claimant))
        true_outcome = bounty.get("true_outcome")

        if report.get("outcome") != true_outcome:
            raise ContractError.UNAUTHORIZED # Incorrect reports receive no payout

        # Check if already claimed
        is_claimed = self.storage.get(("claimed", bounty_id, claimant), False)
        if is_claimed:
            raise ContractError.REPORT_ALREADY_SUBMITTED

        # Mark as claimed
        self.storage.set(("claimed", bounty_id, claimant), True)

        reporters = bounty.get("reporters")
        total_weight = U128(0)
        claimant_weight = U128(0)

        # First identify the earliest correct reporter to grant speed bonus
        earliest_correct = Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF")
        earliest_time = U64(18446744073709551615) # Max U64

        for i in range(len(reporters)):
            rep = reporters.get(i)
            rep_report = self.storage.get(("report", bounty_id, rep))
            if rep_report.get("outcome") == true_outcome:
                ts = rep_report.get("timestamp")
                if ts < earliest_time:
                    earliest_time = ts
                    earliest_correct = rep

        # Calculate total weight and claimant weight
        for i in range(len(reporters)):
            rep = reporters.get(i)
            rep_report = self.storage.get(("report", bounty_id, rep))
            if rep_report.get("outcome") == true_outcome:
                rating = rep_report.get("rating_at_submission")
                weight = U128(rating) * U128(10) # Base weight (multiplier 1.0)
                if rep == earliest_correct:
                    weight = U128(rating) * U128(15) # Speed multiplier 1.5

                total_weight = total_weight + weight
                if rep == claimant:
                    claimant_weight = weight

        if total_weight == U128(0):
            raise ContractError.NO_CORRECT_REPORTERS

        reward = bounty.get("reward_amount")
        payout = (reward * claimant_weight) / total_weight

        token = self.storage.get("token")
        self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), claimant, payout])

        self.env.emit_event("payout_claimed", {
            "bounty_id": bounty_id,
            "claimant": claimant,
            "payout": payout,
        })

        return payout

    @view
    def get_reporter_rating(self, reporter: Address) -> U64:
        """Get the current rating of a reporter."""
        return self.storage.get(("reporter_rating", reporter), U64(1000))

    @view
    def get_bounty(self, bounty_id: U64) -> Map:
        """Get bounty details."""
        return self.storage.get(("bounty", bounty_id))

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
