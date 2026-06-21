"""
Moloch V2 DAO — Tribute-based membership, shares, loot, grace periods, ragequit, and guild kicks.

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
    PROPOSAL_NOT_FOUND = 4
    PROPOSAL_ALREADY_SPONSORED = 5
    PROPOSAL_NOT_SPONSORED = 6
    SPONSOR_REQUIRED = 7
    VOTING_PERIOD_ACTIVE = 8
    VOTING_PERIOD_NOT_STARTED = 9
    VOTING_PERIOD_ENDED = 10
    ALREADY_VOTED = 11
    GRACE_PERIOD_ACTIVE = 12
    PROPOSAL_ALREADY_PROCESSED = 13
    INSUFFICIENT_SHARES_OR_LOOT = 14
    INSUFFICIENT_FUNDS = 15
    CANNOT_RAGEQUIT = 16
    MEMBER_JAILED = 17
    PREVIOUS_PROPOSAL_NOT_PROCESSED = 18
    INVALID_PROPOSAL_DETAILS = 19


class ProposalState:
    SUBMITTED = 0
    SPONSORED = 1
    PROCESSED = 2


class VoteType:
    AGAINST = 0
    FOR = 1


@contract
class MolochDAO:
    """A Moloch V2-style DAO focusing on tribute, shares, loot, and ragequit safety."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        deposit_token: Address,
        voting_period_length: U64,
        grace_period_length: U64,
        proposal_deposit: U128,
        processing_reward: U128,
    ):
        """Initialize the Moloch V2 DAO contract.

        Args:
            admin: Initial owner/creator of the DAO.
            deposit_token: Token used for proposals and guild bank assets.
            voting_period_length: Duration of voting period in seconds.
            grace_period_length: Duration of grace period in seconds.
            proposal_deposit: Amount of deposit token required to sponsor a proposal.
            processing_reward: Reward paid to proposal processor.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        if proposal_deposit < processing_reward:
            raise ContractError.INSUFFICIENT_FUNDS

        self.storage.set("admin", admin)
        self.storage.set("deposit_token", deposit_token)
        self.storage.set("voting_period_length", voting_period_length)
        self.storage.set("grace_period_length", grace_period_length)
        self.storage.set("proposal_deposit", proposal_deposit)
        self.storage.set("processing_reward", processing_reward)

        self.storage.set("proposal_count", U64(0))
        self.storage.set("total_shares", U128(0))
        self.storage.set("total_loot", U128(0))
        self.storage.set("guild_bank_balance", U128(0))
        self.storage.set("processed_proposals_count", U64(0))
        self.storage.set("initialized", True)

        # Creator starts with 1 share to boot the DAO
        self._mint_shares(admin, U128(1))

        self.env.emit_event("initialized", {
            "admin": admin,
            "deposit_token": deposit_token,
            "proposal_deposit": proposal_deposit,
        })

    @external
    def submit_proposal(
        self,
        proposer: Address,
        applicant: Address,
        shares_requested: U128,
        loot_requested: U128,
        tribute_offered: U128,
        payment_requested: U128,
        details: Symbol,
    ) -> U64:
        """Submit a proposal for membership or funding.

        Args:
            proposer: Address submitting the proposal.
            applicant: Address requesting shares/loot or payment.
            shares_requested: Amount of voting shares requested.
            loot_requested: Amount of non-voting loot shares requested.
            tribute_offered: Tribute tokens offered to the guild bank.
            payment_requested: Tokens requested out of the bank.
            details: Symbol describing the proposal.
        """
        self._require_initialized()
        proposer.require_auth()

        # Proposer must deposit the tribute offered immediately
        if tribute_offered > U128(0):
            token = self.storage.get("deposit_token")
            transfer_args = [proposer, self.env.current_contract_address(), tribute_offered]
            success = self.env.invoke_contract(token, "transfer", transfer_args)
            if not success:
                raise ContractError.INSUFFICIENT_FUNDS

        proposal_id = self.storage.get("proposal_count") + U64(1)
        self.storage.set("proposal_count", proposal_id)

        proposal = {
            "id": proposal_id,
            "proposer": proposer,
            "applicant": applicant,
            "shares_requested": shares_requested,
            "loot_requested": loot_requested,
            "tribute_offered": tribute_offered,
            "payment_requested": payment_requested,
            "details": details,
            "sponsor": proposer, # Initialized to proposer, update on sponsor
            "sponsored": False,
            "voting_start": U64(0),
            "voting_end": U64(0),
            "grace_end": U64(0),
            "yes_votes": U128(0),
            "no_votes": U128(0),
            "processed": False,
            "did_pass": False,
            "is_kick": False,
        }

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("proposal_submitted", {
            "proposal_id": proposal_id,
            "proposer": proposer,
            "applicant": applicant,
            "tribute": tribute_offered,
        })

        return proposal_id

    @external
    def sponsor_proposal(self, sponsor: Address, proposal_id: U64):
        """Sponsor a proposal to queue it for voting.

        Args:
            sponsor: A DAO member sponsoring the proposal.
            proposal_id: The proposal ID.
        """
        self._require_initialized()
        sponsor.require_auth()

        # Sponsor must have shares
        sponsor_member = self._get_member(sponsor)
        if sponsor_member["shares"] == U128(0):
            raise ContractError.UNAUTHORIZED

        proposal = self._get_proposal(proposal_id)
        if proposal["sponsored"]:
            raise ContractError.PROPOSAL_ALREADY_SPONSORED

        # Escrow proposal deposit from sponsor
        token = self.storage.get("deposit_token")
        proposal_deposit = self.storage.get("proposal_deposit")
        transfer_args = [sponsor, self.env.current_contract_address(), proposal_deposit]
        success = self.env.invoke_contract(token, "transfer", transfer_args)
        if not success:
            raise ContractError.INSUFFICIENT_FUNDS

        now = self.env.ledger().timestamp()
        voting_period = self.storage.get("voting_period_length")
        grace_period = self.storage.get("grace_period_length")

        proposal["sponsored"] = True
        proposal["sponsor"] = sponsor
        proposal["voting_start"] = now
        proposal["voting_end"] = now + voting_period
        proposal["grace_end"] = now + voting_period + grace_period

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("proposal_sponsored", {
            "proposal_id": proposal_id,
            "sponsor": sponsor,
            "voting_start": now,
        })

    @external
    def submit_vote(self, voter: Address, proposal_id: U64, vote_type: U64):
        """Submit a vote on a sponsored proposal.

        Args:
            voter: The DAO member address.
            proposal_id: The proposal ID.
            vote_type: 0 for AGAINST, 1 for FOR.
        """
        self._require_initialized()
        voter.require_auth()

        voter_member = self._get_member(voter)
        if voter_member["shares"] == U128(0) or voter_member["jailed"]:
            raise ContractError.UNAUTHORIZED

        proposal = self._get_proposal(proposal_id)
        if not proposal["sponsored"]:
            raise ContractError.PROPOSAL_NOT_SPONSORED

        now = self.env.ledger().timestamp()
        if now < proposal["voting_start"]:
            raise ContractError.VOTING_PERIOD_NOT_STARTED
        if now >= proposal["voting_end"]:
            raise ContractError.VOTING_PERIOD_ENDED

        already_voted = self.storage.get(("voted", proposal_id, voter), False)
        if already_voted:
            raise ContractError.ALREADY_VOTED

        voting_power = voter_member["shares"]
        if vote_type == VoteType.FOR:
            proposal["yes_votes"] = proposal["yes_votes"] + voting_power
        elif vote_type == VoteType.AGAINST:
            proposal["no_votes"] = proposal["no_votes"] + voting_power
        else:
            raise ContractError.INVALID_PROPOSAL_DETAILS

        self.storage.set(("proposal", proposal_id), proposal)
        self.storage.set(("voted", proposal_id, voter), True)

        self.env.emit_event("vote_submitted", {
            "proposal_id": proposal_id,
            "voter": voter,
            "vote_type": vote_type,
            "weight": voting_power,
        })

    @external
    def process_proposal(self, processor: Address, proposal_id: U64):
        """Process a proposal after voting and grace periods end.

        Args:
            processor: Address triggering finalization.
            proposal_id: The proposal ID.
        """
        self._require_initialized()
        processor.require_auth()

        proposal = self._get_proposal(proposal_id)
        if not proposal["sponsored"]:
            raise ContractError.PROPOSAL_NOT_SPONSORED
        if proposal["processed"]:
            raise ContractError.PROPOSAL_ALREADY_PROCESSED

        now = self.env.ledger().timestamp()
        if now < proposal["grace_end"]:
            raise ContractError.GRACE_PERIOD_ACTIVE

        # Sequential check: proposals must be processed in order
        processed_count = self.storage.get("processed_proposals_count")
        if proposal_id != processed_count + U64(1):
            raise ContractError.PREVIOUS_PROPOSAL_NOT_PROCESSED

        proposal["processed"] = True
        did_pass = proposal["yes_votes"] > proposal["no_votes"]

        # If it's a kick proposal
        if proposal["is_kick"]:
            if did_pass:
                # Jail the applicant (strip shares, convert to loot)
                applicant_member = self._get_member(proposal["applicant"])
                shares = applicant_member["shares"]
                applicant_member["shares"] = U128(0)
                applicant_member["loot"] = applicant_member["loot"] + shares
                applicant_member["jailed"] = True
                self.storage.set(("member", proposal["applicant"]), applicant_member)

                # Update global totals
                total_shares = self.storage.get("total_shares")
                total_loot = self.storage.get("total_loot")
                self.storage.set("total_shares", total_shares - shares)
                self.storage.set("total_loot", total_loot + shares)
            proposal["did_pass"] = did_pass
        else:
            if did_pass:
                proposal["did_pass"] = True
                # Guild bank holds the tribute
                guild_bank_balance = self.storage.get("guild_bank_balance")
                self.storage.set("guild_bank_balance", guild_bank_balance + proposal["tribute_offered"])

                # Mint shares and loot to applicant
                self._mint_shares(proposal["applicant"], proposal["shares_requested"])
                self._mint_loot(proposal["applicant"], proposal["loot_requested"])

                # Disburse payment if requested
                if proposal["payment_requested"] > U128(0):
                    token = self.storage.get("deposit_token")
                    transfer_args = [self.env.current_contract_address(), proposal["applicant"], proposal["payment_requested"]]
                    success = self.env.invoke_contract(token, "transfer", transfer_args)
                    if not success:
                        # Should not fail unless guild bank has insufficient funds
                        # If payment fails, we just don't pass/execute payment
                        proposal["did_pass"] = False
            else:
                # Return tribute to proposer if proposal failed
                if proposal["tribute_offered"] > U128(0):
                    token = self.storage.get("deposit_token")
                    transfer_args = [self.env.current_contract_address(), proposal["proposer"], proposal["tribute_offered"]]
                    self.env.invoke_contract(token, "transfer", transfer_args)

        # Distribute processing reward and return remaining deposit
        token = self.storage.get("deposit_token")
        proposal_deposit = self.storage.get("proposal_deposit")
        processing_reward = self.storage.get("processing_reward")
        sponsor_refund = proposal_deposit - processing_reward

        # Pay processor
        self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), processor, processing_reward])
        # Refund sponsor
        self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), proposal["sponsor"], sponsor_refund])

        self.storage.set(("proposal", proposal_id), proposal)
        self.storage.set("processed_proposals_count", proposal_id)

        self.env.emit_event("proposal_processed", {
            "proposal_id": proposal_id,
            "did_pass": proposal["did_pass"],
        })

    @external
    def ragequit(self, member: Address, shares_to_burn: U128, loot_to_burn: U128):
        """Burn shares/loot for a proportional slice of guild bank funds.

        Args:
            member: Member address calling ragequit.
            shares_to_burn: Amount of voting shares to burn.
            loot_to_burn: Amount of non-voting loot to burn.
        """
        self._require_initialized()
        member.require_auth()

        member_data = self._get_member(member)
        if member_data["shares"] < shares_to_burn or member_data["loot"] < loot_to_burn:
            raise ContractError.INSUFFICIENT_SHARES_OR_LOOT

        total_burn = shares_to_burn + loot_to_burn
        if total_burn == U128(0):
            raise ContractError.CANNOT_RAGEQUIT

        total_shares = self.storage.get("total_shares")
        total_loot = self.storage.get("total_loot")
        total_supply = total_shares + total_loot

        # Calculate proportional amount from guild bank
        token = self.storage.get("deposit_token")
        bank_balance = self.env.invoke_contract(token, "balance", [self.env.current_contract_address()])

        fair_share = (bank_balance * total_burn) / total_supply

        # Deduct from member
        member_data["shares"] = member_data["shares"] - shares_to_burn
        member_data["loot"] = member_data["loot"] - loot_to_burn
        self.storage.set(("member", member), member_data)

        # Deduct from globals
        self.storage.set("total_shares", total_shares - shares_to_burn)
        self.storage.set("total_loot", total_loot - loot_to_burn)

        # Transfer funds to member
        success = self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), member, fair_share])
        if not success:
            raise ContractError.INSUFFICIENT_FUNDS

        self.env.emit_event("ragequit", {
            "member": member,
            "shares_burned": shares_to_burn,
            "loot_burned": loot_to_burn,
            "payout": fair_share,
        })

    @external
    def guild_kick_proposal(self, proposer: Address, member_to_kick: Address, details: Symbol) -> U64:
        """Create a proposal to jail a member and convert their shares to loot.

        Args:
            proposer: Member initiating the kick.
            member_to_kick: Member being kicked.
            details: Symbol detail explanation.
        """
        self._require_initialized()
        proposer.require_auth()

        proposer_member = self._get_member(proposer)
        if proposer_member["shares"] == U128(0):
            raise ContractError.UNAUTHORIZED

        kick_member = self._get_member(member_to_kick)
        if kick_member["shares"] == U128(0) or kick_member["jailed"]:
            raise ContractError.CANNOT_RAGEQUIT

        proposal_id = self.storage.get("proposal_count") + U64(1)
        self.storage.set("proposal_count", proposal_id)

        proposal = {
            "id": proposal_id,
            "proposer": proposer,
            "applicant": member_to_kick,
            "shares_requested": U128(0),
            "loot_requested": U128(0),
            "tribute_offered": U128(0),
            "payment_requested": U128(0),
            "details": details,
            "sponsor": proposer,
            "sponsored": False,
            "voting_start": U64(0),
            "voting_end": U64(0),
            "grace_end": U64(0),
            "yes_votes": U128(0),
            "no_votes": U128(0),
            "processed": False,
            "did_pass": False,
            "is_kick": True,
        }

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("kick_proposed", {
            "proposal_id": proposal_id,
            "proposer": proposer,
            "member_to_kick": member_to_kick,
        })

        return proposal_id

    @view
    def get_member(self, member: Address) -> Map:
        """Retrieve membership info."""
        return self._get_member(member)

    @view
    def get_proposal(self, proposal_id: U64) -> Map:
        """Retrieve proposal info."""
        return self._get_proposal(proposal_id)

    @view
    def get_totals(self) -> Map:
        """Get global stats of the DAO."""
        return {
            "total_shares": self.storage.get("total_shares"),
            "total_loot": self.storage.get("total_loot"),
            "proposal_count": self.storage.get("proposal_count"),
            "processed_count": self.storage.get("processed_proposals_count"),
        }

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _get_member(self, member: Address) -> Map:
        data = self.storage.get(("member", member), None)
        if data is None:
            return {
                "shares": U128(0),
                "loot": U128(0),
                "jailed": False,
            }
        return data

    def _get_proposal(self, proposal_id: U64) -> Map:
        proposal = self.storage.get(("proposal", proposal_id), None)
        if proposal is None:
            raise ContractError.PROPOSAL_NOT_FOUND
        return proposal

    def _mint_shares(self, member: Address, amount: U128):
        if amount == U128(0):
            return
        member_data = self._get_member(member)
        member_data["shares"] = member_data["shares"] + amount
        self.storage.set(("member", member), member_data)

        total_shares = self.storage.get("total_shares")
        self.storage.set("total_shares", total_shares + amount)

    def _mint_loot(self, member: Address, amount: U128):
        if amount == U128(0):
            return
        member_data = self._get_member(member)
        member_data["loot"] = member_data["loot"] + amount
        self.storage.set(("member", member), member_data)

        total_loot = self.storage.get("total_loot")
        self.storage.set("total_loot", total_loot + amount)
