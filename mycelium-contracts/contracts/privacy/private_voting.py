"""
Private Voting System — Commit-reveal voting with encrypted ballot hashes, weight multipliers, and timing verification.

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
    COMMIT_PHASE_CLOSED = 4
    REVEAL_PHASE_CLOSED = 5
    REVEAL_PHASE_NOT_OPEN = 6
    VOTER_NOT_REGISTERED = 7
    ALREADY_VOTED = 8
    NO_COMMITMENT_FOUND = 9
    INVALID_BALLOT_REVEAL = 10
    ALREADY_REVEALED = 11
    INVALID_CHOICE = 12


@contract
class PrivateVotingSystem:
    """Manages commit-reveal secure ballots, weight multipliers, and aggregate result calculations."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        commit_duration: U64,
        reveal_duration: U64
    ):
        """Initialize the private voting contract parameters and duration boundaries."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        now = self.env.ledger().timestamp()

        self.storage.set("admin", admin)
        self.storage.set("commit_deadline", now + commit_duration)
        self.storage.set("reveal_deadline", now + commit_duration + reveal_duration)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "commit_deadline": now + commit_duration,
            "reveal_deadline": now + commit_duration + reveal_duration
        })

    # ------------------------------------------------------------------ #
    #  Admin Operations                                                   #
    # ------------------------------------------------------------------ #

    @external
    def register_voter(self, admin: Address, voter: Address, weight: U64):
        """Register a voter and assign a voting weight multiplier. Only Admin."""
        self._require_admin(admin)
        
        # Verify voting has not started or commit phase has not expired
        now = self.env.ledger().timestamp()
        if now > self.storage.get("commit_deadline"):
            raise ContractError.COMMIT_PHASE_CLOSED

        if weight == U64(0):
            weight = U64(1)

        self.storage.set(("weight", voter), weight)
        self.env.emit_event("voter_registered", {"voter": voter, "weight": weight})

    # ------------------------------------------------------------------ #
    #  Voter Operations                                                   #
    # ------------------------------------------------------------------ #

    @external
    def commit_ballot(self, voter: Address, commitment: Bytes):
        """Commit an encrypted ballot hash (keccak256(choice + salt))."""
        self._require_initialized()
        voter.require_auth()

        now = self.env.ledger().timestamp()
        if now > self.storage.get("commit_deadline"):
            raise ContractError.COMMIT_PHASE_CLOSED

        # Check voter registration (weight must be set)
        weight = self.storage.get(("weight", voter), U64(0))
        if weight == U64(0):
            raise ContractError.VOTER_NOT_REGISTERED

        # Ensure voter hasn't already committed
        if self.storage.get(("has_voted", voter), False):
            raise ContractError.ALREADY_VOTED

        self.storage.set(("commitment", voter), commitment)
        self.storage.set(("has_voted", voter), True)

        self.env.emit_event("ballot_committed", {"voter": voter, "commitment": commitment})

    @external
    def reveal_ballot(
        self,
        voter: Address,
        choice: U64,
        salt: Bytes
    ) -> Bool:
        """Reveal choice and salt, validating commitment and adding weight to total tallies."""
        self._require_initialized()
        voter.require_auth()

        now = self.env.ledger().timestamp()
        commit_deadline = self.storage.get("commit_deadline")
        reveal_deadline = self.storage.get("reveal_deadline")

        if now < commit_deadline:
            raise ContractError.REVEAL_PHASE_NOT_OPEN
        if now > reveal_deadline:
            raise ContractError.REVEAL_PHASE_CLOSED

        # Ensure commitment exists and not yet revealed
        commitment = self.storage.get(("commitment", voter), None)
        if commitment is None:
            raise ContractError.NO_COMMITMENT_FOUND

        if self.storage.get(("revealed", voter), False):
            raise ContractError.ALREADY_REVEALED

        # Validate choice range (e.g. 1 for Yes, 2 for No)
        if choice != U64(1) and choice != U64(2):
            raise ContractError.INVALID_CHOICE

        # Cryptographic verification: hash(choice + salt) == commitment
        expected_hash = self.env.crypto().keccak256(choice, salt)
        if expected_hash != commitment:
            raise ContractError.INVALID_BALLOT_REVEAL

        # Fetch voter weight
        weight = self.storage.get(("weight", voter), U64(1))

        # Tally vote
        tally = self.storage.get(("tally", choice), U64(0))
        self.storage.set(("tally", choice), tally + weight)

        # Mark revealed
        self.storage.set(("revealed", voter), True)

        self.env.emit_event("ballot_revealed", {
            "voter": voter,
            "choice": choice,
            "weight": weight
        })

        return True

    # ------------------------------------------------------------------ #
    #  View Operations                                                    #
    # ------------------------------------------------------------------ #

    @view
    def get_voting_results(self) -> Map:
        """Get vote tallies. Only visible after reveal phase ends."""
        self._require_initialized()
        
        now = self.env.ledger().timestamp()
        reveal_deadline = self.storage.get("reveal_deadline")
        if now < reveal_deadline:
            raise ContractError.REVEAL_PHASE_NOT_OPEN

        yes_votes = self.storage.get(("tally", U64(1)), U64(0))
        no_votes = self.storage.get(("tally", U64(2)), U64(0))

        res = Map()
        res.set(Symbol("yes_votes"), yes_votes)
        res.set(Symbol("no_votes"), no_votes)
        return res

    @view
    def check_voter_state(self, voter: Address) -> Map:
        """Check status of a specific voter."""
        self._require_initialized()
        res = Map()
        res.set(Symbol("weight"), self.storage.get(("weight", voter), U64(0)))
        res.set(Symbol("has_committed"), self.storage.get(("has_voted", voter), False))
        res.set(Symbol("has_revealed"), self.storage.get(("revealed", voter), False))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                   #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        caller.require_auth()
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED
