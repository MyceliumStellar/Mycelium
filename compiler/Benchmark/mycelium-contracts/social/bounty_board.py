"""
Bounty Board — Decentralized task posting, multi-judge evaluation, and reward payouts.

Mycelium Smart Contract for Stellar
Manages bounty postings, solution submission locks, multi-judge approval panels,
and reward payouts to developers/hunters with developer-judge split rates.
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
    BOUNTY_NOT_FOUND = 5
    BOUNTY_CLOSED = 6
    BOUNTY_ACTIVE = 7
    SUBMISSION_CLOSED = 8
    NOT_JUDGE = 9
    ALREADY_VOTED = 10
    SUBMISSION_NOT_APPROVED = 11
    BOUNTY_EXPIRED = 12
    BOUNTY_NOT_EXPIRED = 13
    TRANSFER_FAILED = 14


@contract
class BountyBoard:
    """
    Bounty Board managing task postings, submission hashes,
    multi-judge consensus voting, and automated payouts.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        asset_token: Address,
        min_bounty_reward: U128,
        judge_fee_bps: U64,  # e.g., 500 bps = 5% of bounty reward allocated to judges
    ):
        """Initialize the bounty board."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if min_bounty_reward == 0 or judge_fee_bps > 2000:
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("admin", admin)
        self.storage.set("asset_token", asset_token)
        self.storage.set("min_reward", min_bounty_reward)
        self.storage.set("judge_fee", judge_fee_bps)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "asset_token": asset_token,
            "min_reward": min_bounty_reward,
        })

    @external
    def create_bounty(
        self,
        poster: Address,
        bounty_id: Symbol,
        evidence_hash: Bytes,
        amount: U128,
        judges: Vec,
        deadline_ledger: U64,
    ):
        """Post a new bounty with locked reward asset and set a panel of judges."""
        poster.require_auth()
        self._require_initialized()

        if self.storage.get(f"bounty:{bounty_id}:exists", False):
            raise ContractError.INVALID_PARAMETERS

        min_reward = self.storage.get("min_reward")
        if amount < min_reward:
            raise ContractError.INVALID_PARAMETERS

        current_ledger = self.env.ledger().sequence()
        # Deadline must be in the future (e.g. at least 1,000 ledgers from now)
        if deadline_ledger <= current_ledger + U64(1000):
            raise ContractError.INVALID_PARAMETERS

        judges_count = len(judges)
        if judges_count == 0 or judges_count > 5:  # Limit judges array
            raise ContractError.INVALID_PARAMETERS

        # Transfer bounty amount from poster to board contract
        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, poster, self.env.current_contract(), amount)

        # Store bounty details
        prefix = f"bounty:{bounty_id}"
        self.storage.set(f"{prefix}:exists", True)
        self.storage.set(f"{prefix}:poster", poster)
        self.storage.set(f"{prefix}:amount", amount)
        self.storage.set(f"{prefix}:evidence", evidence_hash)
        self.storage.set(f"{prefix}:deadline", deadline_ledger)
        self.storage.set(f"{prefix}:judges", judges)
        self.storage.set(f"{prefix}:resolved", False)
        self.storage.set(f"{prefix}:submissions", Vec())

        self.env.emit_event("bounty_created", {
            "bounty_id": bounty_id,
            "poster": poster,
            "amount": amount,
            "deadline": deadline_ledger,
        })

    @external
    def submit_solution(
        self,
        hunter: Address,
        bounty_id: Symbol,
        solution_hash: Bytes,
    ):
        """Hunter submits solution hash before the deadline."""
        hunter.require_auth()
        self._require_initialized()

        prefix = f"bounty:{bounty_id}"
        if not self.storage.get(f"{prefix}:exists", False):
            raise ContractError.BOUNTY_NOT_FOUND

        if self.storage.get(f"{prefix}:resolved", False):
            raise ContractError.BOUNTY_CLOSED

        deadline = self.storage.get(f"{prefix}:deadline")
        current_ledger = self.env.ledger().sequence()
        if current_ledger > deadline:
            raise ContractError.BOUNTY_EXPIRED

        # Add solution to bounty list
        submissions = self.storage.get(f"{prefix}:submissions")
        # Check if already submitted by this hunter
        for i in range(len(submissions)):
            if submissions[i] == hunter:
                raise ContractError.INVALID_PARAMETERS

        submissions.append(hunter)
        self.storage.set(f"{prefix}:submissions", submissions)

        # Set submission hash and vote counters
        self.storage.set(f"solution:{bounty_id}:{hunter}:hash", solution_hash)
        self.storage.set(f"solution:{bounty_id}:{hunter}:yes_votes", U64(0))
        self.storage.set(f"solution:{bounty_id}:{hunter}:no_votes", U64(0))

        self.env.emit_event("solution_submitted", {
            "bounty_id": bounty_id,
            "hunter": hunter,
            "solution_hash": solution_hash,
        })

    @external
    def vote_submission(
        self,
        judge: Address,
        bounty_id: Symbol,
        hunter: Address,
        approve: Bool,
    ):
        """A judge casts a vote on a hunter's submitted solution."""
        judge.require_auth()
        self._require_initialized()

        prefix = f"bounty:{bounty_id}"
        if not self.storage.get(f"{prefix}:exists", False):
            raise ContractError.BOUNTY_NOT_FOUND

        if self.storage.get(f"{prefix}:resolved", False):
            raise ContractError.BOUNTY_CLOSED

        # Verify judge is on the panel
        judges = self.storage.get(f"{prefix}:judges")
        is_judge = False
        for i in range(len(judges)):
            if judges[i] == judge:
                is_judge = True
                break

        if not is_judge:
            raise ContractError.NOT_JUDGE

        # Check if judge already voted for this solution
        vote_key = f"vote:{bounty_id}:{hunter}:{judge}"
        if self.storage.get(vote_key, False):
            raise ContractError.ALREADY_VOTED

        # Record vote
        self.storage.set(vote_key, True)

        sol_prefix = f"solution:{bounty_id}:{hunter}"
        if approve:
            yes_v = self.storage.get(f"{sol_prefix}:yes_votes", U64(0))
            self.storage.set(f"{sol_prefix}:yes_votes", yes_v + 1)
        else:
            no_v = self.storage.get(f"{sol_prefix}:no_votes", U64(0))
            self.storage.set(f"{sol_prefix}:no_votes", no_v + 1)

        self.env.emit_event("judge_vote_cast", {
            "bounty_id": bounty_id,
            "judge": judge,
            "hunter": hunter,
            "approve": approve,
        })

    @external
    def claim_payout(self, caller: Address, bounty_id: Symbol, winner: Address):
        """Settle bounty reward distribution to winning hunter and judges panel."""
        caller.require_auth()
        self._require_initialized()

        prefix = f"bounty:{bounty_id}"
        if not self.storage.get(f"{prefix}:exists", False):
            raise ContractError.BOUNTY_NOT_FOUND

        if self.storage.get(f"{prefix}:resolved", False):
            raise ContractError.BOUNTY_CLOSED

        # Check judge majority approval (Requires yes_votes > judges_count / 2)
        judges = self.storage.get(f"{prefix}:judges")
        judges_count = len(judges)

        yes_votes = self.storage.get(f"solution:{bounty_id}:{winner}:yes_votes", U64(0))

        # Majority check
        required_votes = (judges_count // 2) + 1
        if yes_votes < required_votes:
            raise ContractError.SUBMISSION_NOT_APPROVED

        # Calculate splits
        total_amount = self.storage.get(f"{prefix}:amount")
        fee_bps = self.storage.get("judge_fee")

        judge_total_fee = (total_amount * U128(fee_bps)) // U128(10000)
        hunter_share = total_amount - judge_total_fee

        asset_token = self.storage.get("asset_token")

        # Pay judges
        if judge_total_fee > 0:
            fee_per_judge = judge_total_fee // U128(judges_count)
            for i in range(judges_count):
                self.env.transfer(asset_token, self.env.current_contract(), judges[i], fee_per_judge)

        # Pay winning hunter
        if hunter_share > 0:
            self.env.transfer(asset_token, self.env.current_contract(), winner, hunter_share)

        self.storage.set(f"{prefix}:resolved", True)

        self.env.emit_event("bounty_resolved", {
            "bounty_id": bounty_id,
            "winner": winner,
            "hunter_share": hunter_share,
            "judge_fees": judge_total_fee,
        })

    @external
    def claim_refund(self, poster: Address, bounty_id: Symbol):
        """Poster claims refund if bounty deadline expires without approved solutions."""
        poster.require_auth()
        self._require_initialized()

        prefix = f"bounty:{bounty_id}"
        if not self.storage.get(f"{prefix}:exists", False):
            raise ContractError.BOUNTY_NOT_FOUND

        if self.storage.get(f"{prefix}:resolved", False):
            raise ContractError.BOUNTY_CLOSED

        bounty_poster = self.storage.get(f"{prefix}:poster")
        if bounty_poster != poster:
            raise ContractError.UNAUTHORIZED

        current_ledger = self.env.ledger().sequence()
        deadline = self.storage.get(f"{prefix}:deadline")
        if current_ledger <= deadline:
            raise ContractError.BOUNTY_NOT_EXPIRED

        amount = self.storage.get(f"{prefix}:amount")
        self.storage.set(f"{prefix}:resolved", True)

        # Return locked funds to poster
        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, self.env.current_contract(), poster, amount)

        self.env.emit_event("bounty_refunded", {
            "bounty_id": bounty_id,
            "poster": poster,
            "refunded_amount": amount,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_bounty(self, bounty_id: Symbol) -> Map:
        """Get public bounty parameters and list of submission hunter addresses."""
        prefix = f"bounty:{bounty_id}"
        if not self.storage.get(f"{prefix}:exists", False):
            raise ContractError.BOUNTY_NOT_FOUND

        return {
            "poster": self.storage.get(f"{prefix}:poster"),
            "amount": self.storage.get(f"{prefix}:amount"),
            "deadline": self.storage.get(f"{prefix}:deadline"),
            "judges": self.storage.get(f"{prefix}:judges"),
            "resolved": self.storage.get(f"{prefix}:resolved"),
            "submissions": self.storage.get(f"{prefix}:submissions"),
        }

    @view
    def get_submission(self, bounty_id: Symbol, hunter: Address) -> Map:
        """Get details and judge vote scores of a solution."""
        prefix = f"solution:{bounty_id}:{hunter}"
        return {
            "hash": self.storage.get(f"{prefix}:hash"),
            "yes_votes": self.storage.get(f"{prefix}:yes_votes", U64(0)),
            "no_votes": self.storage.get(f"{prefix}:no_votes", U64(0)),
        }

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
