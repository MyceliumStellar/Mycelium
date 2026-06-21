"""
Resolution Oracle — Reporter registry, dispute appeals, jury selection, voting stakes, resolution logs.

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
    REPORTER_ALREADY_EXISTS = 4
    REPORTER_NOT_FOUND = 5
    REPORT_ALREADY_SUBMITTED = 6
    REPORT_NOT_FOUND = 7
    APPEAL_WINDOW_EXPIRED = 8
    ALREADY_APPEALED = 9
    NOT_APPEALED = 10
    JURY_VOTING_ENDED = 11
    JURY_VOTING_ACTIVE = 12
    ALREADY_VOTED = 13
    INSUFFICIENT_STAKE = 14
    INSUFFICIENT_BALANCE = 15
    ZERO_AMOUNT = 16


class ReportState:
    SUBMITTED = 0
    APPEALED = 1
    FINALIZED = 2


@contract
class ResolutionOracle:
    """A decentralized oracle system with reporter registry, dispute appeals, and jury-based resolution."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        staking_token: Address,
        appeal_duration: U64,
        appeal_bond: U128,
        jury_voting_duration: U64,
    ):
        """Initialize the decentralized oracle registry.

        Args:
            admin: Admin address.
            staking_token: Token used for jury staking.
            appeal_duration: Time in seconds during which a report can be appealed.
            appeal_bond: Bond amount in staking tokens required to appeal.
            jury_voting_duration: Time allowed for jury voting.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("token", staking_token)
        self.storage.set("appeal_duration", appeal_duration)
        self.storage.set("appeal_bond", appeal_bond)
        self.storage.set("jury_voting_duration", jury_voting_duration)

        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "staking_token": staking_token,
        })

    @external
    def add_reporter(self, admin: Address, reporter: Address):
        """Register a new authorized reporter. Only admin.

        Args:
            admin: Admin.
            reporter: Reporter to authorize.
        """
        self._require_initialized()
        self._require_admin(admin)

        self.storage.set(("reporter", reporter), True)
        self.env.emit_event("reporter_added", {"reporter": reporter})

    @external
    def remove_reporter(self, admin: Address, reporter: Address):
        """Remove a reporter. Only admin.

        Args:
            admin: Admin.
            reporter: Reporter to remove.
        """
        self._require_initialized()
        self._require_admin(admin)

        if not self.storage.get(("reporter", reporter), False):
            raise ContractError.REPORTER_NOT_FOUND

        self.storage.set(("reporter", reporter), False)
        self.env.emit_event("reporter_removed", {"reporter": reporter})

    @external
    def submit_report(self, reporter: Address, market: Address, outcome: Symbol):
        """Submit the initial resolution report for a prediction market.

        Args:
            reporter: Registered reporter.
            market: Address of the target market.
            outcome: Proposed outcome.
        """
        self._require_initialized()
        reporter.require_auth()

        if not self.storage.get(("reporter", reporter), False):
            raise ContractError.UNAUTHORIZED

        existing_report = self.storage.get(("report", market), None)
        if existing_report is not None:
            raise ContractError.REPORT_ALREADY_SUBMITTED

        now = self.env.ledger().timestamp()
        appeal_window = now + self.storage.get("appeal_duration")

        report = Map()
        report.set("reporter", reporter)
        report.set("proposed_outcome", outcome)
        report.set("state", ReportState.SUBMITTED)
        report.set("appeal_end", appeal_window)
        report.set("final_outcome", outcome)

        self.storage.set(("report", market), report)

        self.env.emit_event("report_submitted", {
            "market": market,
            "reporter": reporter,
            "proposed_outcome": outcome,
            "appeal_end": appeal_window,
        })

    @external
    def appeal_report(self, appellant: Address, market: Address, dispute_outcome: Symbol):
        """Appeal a submitted report by posting a bond. This triggers jury selection.

        Args:
            appellant: User appealing.
            market: Target market address.
            dispute_outcome: Outcome believed to be correct by appellant.
        """
        self._require_initialized()
        appellant.require_auth()

        report = self.storage.get(("report", market), None)
        if report is None:
            raise ContractError.REPORT_NOT_FOUND

        if report.get("state") != ReportState.SUBMITTED:
            raise ContractError.ALREADY_APPEALED

        now = self.env.ledger().timestamp()
        if now > report.get("appeal_end"):
            raise ContractError.APPEAL_WINDOW_EXPIRED

        bond = self.storage.get("appeal_bond")
        token = self.storage.get("token")

        # Escrow bond
        success = self.env.invoke_contract(token, "transfer", [appellant, self.env.current_contract_address(), bond])
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        # Update report state to APPEALED
        report.set("state", ReportState.APPEALED)
        self.storage.set(("report", market), report)

        # Setup Jury voting parameters
        jury = Map()
        jury.set("appellant", appellant)
        jury.set("dispute_outcome", dispute_outcome)
        jury.set("vote_end", now + self.storage.get("jury_voting_duration"))
        jury.set("votes_prop", U128(0)) # Votes in favor of initial proposal
        jury.set("votes_disp", U128(0)) # Votes in favor of dispute outcome
        jury.set("total_staked", U128(0))

        self.storage.set(("jury", market), jury)

        self.env.emit_event("report_appealed", {
            "market": market,
            "appellant": appellant,
            "dispute_outcome": dispute_outcome,
        })

    @external
    def cast_jury_vote(self, juror: Address, market: Address, support_dispute: Bool, stake_amount: U128) -> U128:
        """Join jury and vote on the outcome by staking governance tokens.

        Args:
            juror: Voter.
            market: Appealed market.
            support_dispute: True to vote for appellant's dispute, False for initial reporter.
            stake_amount: Amount of tokens to stake.
        """
        self._require_initialized()
        juror.require_auth()

        report = self.storage.get(("report", market), None)
        if report is None or report.get("state") != ReportState.APPEALED:
            raise ContractError.NOT_APPEALED

        jury = self.storage.get(("jury", market))
        now = self.env.ledger().timestamp()
        if now > jury.get("vote_end"):
            raise ContractError.JURY_VOTING_ENDED

        if stake_amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        # Verify no double voting
        has_voted = self.storage.get(("voted", market, juror), False)
        if has_voted:
            raise ContractError.ALREADY_VOTED

        token = self.storage.get("token")
        # Escrow stake
        success = self.env.invoke_contract(token, "transfer", [juror, self.env.current_contract_address(), stake_amount])
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        # Update votes
        if support_dispute:
            jury.set("votes_disp", jury.get("votes_disp") + stake_amount)
        else:
            jury.set("votes_prop", jury.get("votes_prop") + stake_amount)

        jury.set("total_staked", jury.get("total_staked") + stake_amount)
        self.storage.set(("jury", market), jury)

        # Store juror record
        self.storage.set(("voted", market, juror), True)
        self.storage.set(("juror_stake", market, juror), stake_amount)
        self.storage.set(("juror_choice", market, juror), support_dispute)

        self.env.emit_event("jury_vote_cast", {
            "market": market,
            "juror": juror,
            "support_dispute": support_dispute,
            "stake": stake_amount,
        })

        return stake_amount

    @external
    def finalize_report(self, caller: Address, market: Address):
        """Finalize the report. If unresolved and appeal duration has expired, finalize reporter's outcome.
        If appealed and jury voting ended, determine winning outcome and slash losing side.

        Args:
            caller: Trigger address.
            market: Target market.
        """
        self._require_initialized()
        caller.require_auth()

        report = self.storage.get(("report", market), None)
        if report is None:
            raise ContractError.REPORT_NOT_FOUND

        if report.get("state") == ReportState.FINALIZED:
            raise ContractError.REPORT_ALREADY_SUBMITTED

        now = self.env.ledger().timestamp()

        if report.get("state") == ReportState.SUBMITTED:
            if now <= report.get("appeal_end"):
                raise ContractError.JURY_VOTING_ACTIVE
            # Finalize proposed outcome directly
            report.set("state", ReportState.FINALIZED)
            self.storage.set(("report", market), report)
            self.env.emit_event("report_finalized", {
                "market": market,
                "outcome": report.get("final_outcome"),
            })

        elif report.get("state") == ReportState.APPEALED:
            jury = self.storage.get(("jury", market))
            if now <= jury.get("vote_end"):
                raise ContractError.JURY_VOTING_ACTIVE

            votes_prop = jury.get("votes_prop")
            votes_disp = jury.get("votes_disp")
            token = self.storage.get("token")
            bond = self.storage.get("appeal_bond")

            final_winner = Symbol("")
            if votes_disp > votes_prop:
                # Appeal wins, final outcome is the dispute outcome
                final_winner = jury.get("dispute_outcome")
                report.set("final_outcome", final_winner)
                # Appellant gets bond back
                self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), jury.get("appellant"), bond])
                self.storage.set(("jury_winner", market), True) # True means dispute side won
            else:
                # Initial report wins
                final_winner = report.get("proposed_outcome")
                # Appellant's bond is forfeit and distributed to the winning jurors
                # (added to the redistribution pool)
                self.storage.set(("jury_winner", market), False)

            report.set("state", ReportState.FINALIZED)
            self.storage.set(("report", market), report)

            self.env.emit_event("report_finalized", {
                "market": market,
                "outcome": final_winner,
            })

    @external
    def claim_jury_rewards(self, juror: Address, market: Address) -> U128:
        """Claim rewards for jurors who voted on the correct side of an appeal.
        Winning jurors receive their stake back plus a share of the losing jurors' stake and appellant bond.

        Args:
            juror: Juror address.
            market: Resolved market.
        """
        self._require_initialized()
        juror.require_auth()

        report = self.storage.get(("report", market), None)
        if report is None or report.get("state") != ReportState.FINALIZED:
            raise ContractError.MARKET_NOT_RESOLVED

        jury = self.storage.get(("jury", market), None)
        if jury is None:
            raise ContractError.REPORT_NOT_FOUND

        has_voted = self.storage.get(("voted", market, juror), False)
        if not has_voted:
            raise ContractError.UNAUTHORIZED

        juror_stake = self.storage.get(("juror_stake", market, juror), U128(0))
        if juror_stake == U128(0):
            raise ContractError.ZERO_AMOUNT

        juror_choice = self.storage.get(("juror_choice", market, juror))
        jury_winner = self.storage.get(("jury_winner", market))

        # Clear juror stake to prevent double claim
        self.storage.set(("juror_stake", market, juror), U128(0))

        token = self.storage.get("token")

        if juror_choice == jury_winner:
            # Juror won! Calculate share of losing stake + appellant bond
            winning_votes = U128(0)
            losing_votes = U128(0)
            if jury_winner:
                winning_votes = jury.get("votes_disp")
                losing_votes = jury.get("votes_prop")
            else:
                winning_votes = jury.get("votes_prop")
                losing_votes = jury.get("votes_disp")

            bond_pool = U128(0)
            if not jury_winner:
                # Appellant lost, so bond goes to winning jurors
                bond_pool = self.storage.get("appeal_bond")

            payout = juror_stake + ((juror_stake * (losing_votes + bond_pool)) / winning_votes)
            self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), juror, payout])

            self.env.emit_event("jury_reward_claimed", {
                "market": market,
                "juror": juror,
                "payout": payout,
            })
            return payout
        else:
            # Juror lost, stake was slashed
            self.env.emit_event("jury_slashed", {
                "market": market,
                "juror": juror,
                "slashed_amount": juror_stake,
            })
            return U128(0)

    @view
    def get_report(self, market: Address) -> Map:
        """Get report data."""
        return self.storage.get(("report", market))

    @view
    def get_jury(self, market: Address) -> Map:
        """Get jury details."""
        return self.storage.get(("jury", market))

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
