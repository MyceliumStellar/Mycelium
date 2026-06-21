"""
Constitutional DAO — Immutable on-chain constitution, 66%+ amendment supermajority, versioning, and judicial reviews.

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
    ARTICLE_NOT_FOUND = 4
    PROPOSAL_NOT_FOUND = 5
    INVALID_STATE = 6
    ALREADY_VOTED = 7
    SUPERMAJORITY_NOT_MET = 8
    STRUCK_DOWN = 9
    INVALID_JUDGE = 10
    DUPLICATE_JUDGE = 11


class AmendmentType:
    ADD = 0
    MODIFY = 1
    DELETE = 2


class ProposalState:
    ACTIVE = 0
    DEFEATED = 1
    SUCCEEDED = 2
    EXECUTED = 3
    STRUCK_DOWN = 4


@contract
class ConstitutionalDAO:
    """A DAO contract tracking an on-chain constitution, versions, supermajority amendments, and judicial reviews."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        deposit_token: Address,
        initial_articles: Vec,
        court_judges: Vec,
        voting_duration: U64,
        quorum_bps: U64,
    ):
        """Initialize the Constitution DAO with articles, judges, and parameters.

        Args:
            admin: Setup admin.
            deposit_token: Token for voting weight.
            initial_articles: Vec of Symbol text representing the original articles.
            court_judges: Vec of judge Addresses for judicial review.
            voting_duration: Voting duration in seconds.
            quorum_bps: Quorum in basis points.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("token", deposit_token)
        self.storage.set("version", U64(1))
        self.storage.set("voting_duration", voting_duration)
        self.storage.set("quorum_bps", quorum_bps)

        # Register articles
        art_count = len(initial_articles)
        self.storage.set("articles_count", U64(art_count))
        for i in range(art_count):
            self.storage.set(("article", U64(i + 1)), initial_articles[i])

        # Register judges
        j_len = len(court_judges)
        if j_len == 0:
            raise ContractError.INVALID_JUDGE
        self.storage.set("judges_count", U64(j_len))
        for i in range(j_len):
            judge = court_judges[i]
            if self.storage.get(("judge", judge), False):
                raise ContractError.DUPLICATE_JUDGE
            self.storage.set(("judge", judge), True)

        self.storage.set("proposal_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "version": U64(1),
            "articles_count": U64(art_count),
        })

    @external
    def propose_amendment(
        self,
        proposer: Address,
        amendment_type: U64,
        target_article_id: U64,
        new_text: Symbol,
        description: Symbol,
    ) -> U64:
        """Propose a modification, addition, or deletion to the constitution.

        Args:
            proposer: Address of proposer.
            amendment_type: 0 for ADD, 1 for MODIFY, 2 for DELETE.
            target_article_id: Index of the article (1-indexed). Required for MODIFY and DELETE.
            new_text: Text Symbol for ADD or MODIFY.
            description: Description symbol.
        """
        self._require_initialized()
        proposer.require_auth()

        # Proposer must have voting weight
        power = self._get_voting_power(proposer)
        if power == U128(0):
            raise ContractError.UNAUTHORIZED

        art_count = self.storage.get("articles_count")

        if amendment_type == AmendmentType.MODIFY or amendment_type == AmendmentType.DELETE:
            if target_article_id == U64(0) or target_article_id > art_count:
                raise ContractError.ARTICLE_NOT_FOUND

        proposal_id = self.storage.get("proposal_count") + U64(1)
        self.storage.set("proposal_count", proposal_id)

        now = self.env.ledger().timestamp()
        vote_end = now + self.storage.get("voting_duration")

        proposal = {
            "id": proposal_id,
            "proposer": proposer,
            "type": amendment_type,
            "article_id": target_article_id,
            "new_text": new_text,
            "description": description,
            "vote_end": vote_end,
            "votes_for": U128(0),
            "votes_against": U128(0),
            "challenge_count": U64(0),
            "state": ProposalState.ACTIVE,
        }

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("amendment_proposed", {
            "proposal_id": proposal_id,
            "proposer": proposer,
            "type": amendment_type,
            "article_id": target_article_id,
        })

        return proposal_id

    @external
    def cast_vote(self, voter: Address, proposal_id: U64, vote_type: U64):
        """Vote on a constitutional amendment.

        Args:
            voter: DAO voter.
            proposal_id: Proposal ID.
            vote_type: 0 for AGAINST, 1 for FOR.
        """
        self._require_initialized()
        voter.require_auth()

        proposal = self._get_proposal(proposal_id)
        if proposal["state"] != ProposalState.ACTIVE:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        if now >= proposal["vote_end"]:
            raise ContractError.VOTING_ENDED

        already_voted = self.storage.get(("voted", proposal_id, voter), False)
        if already_voted:
            raise ContractError.ALREADY_VOTED

        voting_power = self._get_voting_power(voter)
        if voting_power == U128(0):
            raise ContractError.UNAUTHORIZED

        if vote_type == U64(1):
            proposal["votes_for"] = proposal["votes_for"] + voting_power
        elif vote_type == U64(0):
            proposal["votes_against"] = proposal["votes_against"] + voting_power
        else:
            raise ContractError.INVALID_STATE

        self.storage.set(("proposal", proposal_id), proposal)
        self.storage.set(("voted", proposal_id, voter), True)

        self.env.emit_event("vote_cast", {
            "proposal_id": proposal_id,
            "voter": voter,
            "vote_type": vote_type,
            "weight": voting_power,
        })

    @external
    def submit_judicial_challenge(self, judge: Address, proposal_id: U64):
        """Judge files a constitutional challenge against an amendment. Only judge.

        Args:
            judge: Registered judge address.
            proposal_id: Proposal ID.
        """
        self._require_initialized()
        judge.require_auth()

        if not self.storage.get(("judge", judge), False):
            raise ContractError.UNAUTHORIZED

        proposal = self._get_proposal(proposal_id)
        if proposal["state"] != ProposalState.ACTIVE:
            raise ContractError.INVALID_STATE

        already_challenged = self.storage.get(("judge_challenged", proposal_id, judge), False)
        if already_challenged:
            raise ContractError.ALREADY_VOTED

        proposal["challenge_count"] = proposal["challenge_count"] + U64(1)
        self.storage.set(("judge_challenged", proposal_id, judge), True)

        # Check if majority of judges have challenged to strike down proposal
        judges_count = self.storage.get("judges_count")
        required_challenges = (judges_count / U64(2)) + U64(1)

        if proposal["challenge_count"] >= required_challenges:
            proposal["state"] = ProposalState.STRUCK_DOWN

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("proposal_challenged", {
            "proposal_id": proposal_id,
            "judge": judge,
            "challenge_count": proposal["challenge_count"],
            "struck_down": proposal["state"] == ProposalState.STRUCK_DOWN,
        })

    @external
    def execute_amendment(self, executor: Address, proposal_id: U64):
        """Apply amendment changes if voting succeeded and supermajority was reached.

        Args:
            executor: Trigger address.
            proposal_id: Proposal ID.
        """
        self._require_initialized()
        executor.require_auth()

        proposal = self._get_proposal(proposal_id)
        if proposal["state"] == ProposalState.STRUCK_DOWN:
            raise ContractError.STRUCK_DOWN
        if proposal["state"] != ProposalState.ACTIVE:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        if now < proposal["vote_end"]:
            raise ContractError.INVALID_STATE

        # Evaluate votes
        total_supply = self._get_total_supply()
        quorum_bps = self.storage.get("quorum_bps")
        required_quorum = (total_supply * U128(quorum_bps)) / U128(10000)

        total_votes = proposal["votes_for"] + proposal["votes_against"]
        quorum_met = total_votes >= required_quorum

        # Supermajority: yes votes must be at least 66% (2/3) of votes cast
        # yes_votes * 100 / total_votes >= 66
        # Or, using integer math: yes_votes * 3 >= total_votes * 2
        supermajority_met = (proposal["votes_for"] * U128(3)) >= (total_votes * U128(2))

        passed = quorum_met and supermajority_met and (proposal["votes_for"] > U128(0))

        if not passed:
            proposal["state"] = ProposalState.DEFEATED
            self.storage.set(("proposal", proposal_id), proposal)
            raise ContractError.SUPERMAJORITY_NOT_MET

        # Apply Constitution amendments
        version = self.storage.get("version")
        art_count = self.storage.get("articles_count")

        a_type = proposal["type"]
        if a_type == AmendmentType.ADD:
            new_art_id = art_count + U64(1)
            self.storage.set(("article", new_art_id), proposal["new_text"])
            self.storage.set("articles_count", new_art_id)
        elif a_type == AmendmentType.MODIFY:
            self.storage.set(("article", proposal["article_id"]), proposal["new_text"])
        elif a_type == AmendmentType.DELETE:
            # Shift downstream articles to keep sequence or just delete content
            self.storage.set(("article", proposal["article_id"]), Symbol(b"DELETED"))

        # Increment constitution version
        new_version = version + U64(1)
        self.storage.set("version", new_version)

        proposal["state"] = ProposalState.EXECUTED
        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("constitution_updated", {
            "proposal_id": proposal_id,
            "new_version": new_version,
            "amendment_type": a_type,
        })

    @view
    def get_article(self, article_id: U64) -> Symbol:
        """Get text of a specific article (1-indexed)."""
        article = self.storage.get(("article", article_id), None)
        if article is None:
            raise ContractError.ARTICLE_NOT_FOUND
        return article

    @view
    def get_constitution_info(self) -> Map:
        """Get version and number of articles."""
        return {
            "version": self.storage.get("version"),
            "articles_count": self.storage.get("articles_count"),
            "judges_count": self.storage.get("judges_count"),
        }

    @view
    def get_proposal(self, proposal_id: U64) -> Map:
        """Get proposal details."""
        proposal = self._get_proposal(proposal_id)
        proposal["computed_state"] = self._compute_state(proposal)
        return proposal

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _get_proposal(self, proposal_id: U64) -> Map:
        proposal = self.storage.get(("proposal", proposal_id), None)
        if proposal is None:
            raise ContractError.PROPOSAL_NOT_FOUND
        return proposal

    def _compute_state(self, proposal: Map) -> U64:
        if proposal["state"] == ProposalState.STRUCK_DOWN:
            return ProposalState.STRUCK_DOWN
        if proposal["state"] == ProposalState.EXECUTED:
            return ProposalState.EXECUTED

        now = self.env.ledger().timestamp()
        if now < proposal["vote_end"]:
            return ProposalState.ACTIVE

        # Evaluate votes
        total_supply = self._get_total_supply()
        quorum_bps = self.storage.get("quorum_bps")
        required_quorum = (total_supply * U128(quorum_bps)) / U128(10000)

        total_votes = proposal["votes_for"] + proposal["votes_against"]
        quorum_met = total_votes >= required_quorum
        supermajority_met = (proposal["votes_for"] * U128(3)) >= (total_votes * U128(2))

        if quorum_met and supermajority_met and (proposal["votes_for"] > U128(0)):
            return ProposalState.SUCCEEDED
        else:
            return ProposalState.DEFEATED

    def _get_voting_power(self, voter: Address) -> U128:
        token = self.storage.get("token")
        return self.env.invoke_contract(token, "balance", [voter])

    def _get_total_supply(self) -> U128:
        token = self.storage.get("token")
        return self.env.invoke_contract(token, "total_supply", [])
