"""
Tournament System — Single-elimination brackets, entry fees, match confirmations, timeouts, and referee resolution.

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
    TRANSFER_FAILED = 4
    TOURNAMENT_NOT_FOUND = 5
    REGISTRATION_CLOSED = 6
    ALREADY_REGISTERED = 7
    INSUFFICIENT_PLAYERS = 8
    ROUND_NOT_COMPLETE = 9
    MATCH_NOT_FOUND = 10
    MATCH_COMPLETED = 11
    CONFLICTING_REPORTS = 12
    DEADLINE_NOT_PASSED = 13
    INVALID_MAX_PLAYERS = 14
    INVALID_TOURNAMENT_STATUS = 15


class TournamentStatus:
    REGISTRATION = 1
    ACTIVE = 2
    COMPLETED = 3
    CANCELLED = 4


@contract
class TournamentSystem:
    """Manages gaming tournaments, entry fees, bracket structure, player reporting, and time-out forfeits."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, gold_token: Address):
        """Initialize the Tournament System contract."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("gold_token", gold_token)
        self.storage.set("tournament_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin, "gold_token": gold_token})

    # ------------------------------------------------------------------ #
    #  Admin / Referee Actions                                           #
    # ------------------------------------------------------------------ #

    @external
    def create_tournament(
        self,
        admin: Address,
        name: Symbol,
        entry_fee: U128,
        max_players: U64,
        duration_per_round: U64
    ) -> U64:
        """Create a new tournament. Max players must be a power of 2 (4, 8, 16, 32, 64). Only Admin."""
        self._require_admin(admin)

        # Check if max_players is power of 2
        if not self._is_power_of_two(max_players) or max_players < U64(4):
            raise ContractError.INVALID_MAX_PLAYERS

        t_id = self.storage.get("tournament_count") + U64(1)
        self.storage.set("tournament_count", t_id)

        tournament = {
            "id": t_id,
            "name": name,
            "entry_fee": entry_fee,
            "max_players": max_players,
            "player_count": U64(0),
            "prize_pool": U128(0),
            "status": U64(TournamentStatus.REGISTRATION),
            "current_round": U64(0),
            "round_deadline": U64(0),
            "duration_per_round": duration_per_round
        }

        self.storage.set(("tournament", t_id), tournament)
        self.storage.set(("tournament_players", t_id), Vec())

        self.env.emit_event("tournament_created", {
            "id": t_id,
            "name": name,
            "entry_fee": entry_fee,
            "max_players": max_players
        })

        return t_id

    @external
    def set_referee(self, admin: Address, referee: Address, status: Bool):
        """Set authorization status of a referee. Only Admin."""
        self._require_admin(admin)
        self.storage.set(("referee", referee), status)
        self.env.emit_event("referee_status_updated", {"referee": referee, "status": status})

    @external
    def resolve_match_referee(
        self,
        referee: Address,
        t_id: U64,
        round_id: U64,
        match_index: U64,
        winner: Address
    ):
        """Overrule or resolve a match conflict. Only Referee or Admin."""
        self._require_initialized()
        referee.require_auth()
        self._require_referee(referee)

        match_key = ("match", t_id, round_id, match_index)
        m = self.storage.get(match_key, None)
        if m is None:
            raise ContractError.MATCH_NOT_FOUND
        if m["completed"]:
            raise ContractError.MATCH_COMPLETED

        # Ensure winner is one of the players
        if winner != m["player1"] and winner != m["player2"]:
            raise ContractError.UNAUTHORIZED

        m["winner"] = winner
        m["completed"] = True
        self.storage.set(match_key, m)

        self.env.emit_event("match_resolved", {
            "tournament_id": t_id,
            "round_id": round_id,
            "match_index": match_index,
            "winner": winner,
            "method": Symbol("referee")
        })

    # ------------------------------------------------------------------ #
    #  Player Actions                                                     #
    # ------------------------------------------------------------------ #

    @external
    def register_player(self, player: Address, t_id: U64):
        """Join a tournament by paying the entry fee."""
        self._require_initialized()
        player.require_auth()

        t = self.storage.get(("tournament", t_id), None)
        if t is None:
            raise ContractError.TOURNAMENT_NOT_FOUND
        if t["status"] != U64(TournamentStatus.REGISTRATION):
            raise ContractError.REGISTRATION_CLOSED

        players = self.storage.get(("tournament_players", t_id), Vec())

        # Check if already registered
        for i in range(len(players)):
            if players[i] == player:
                raise ContractError.ALREADY_REGISTERED

        # Charge entry fee
        fee = t["entry_fee"]
        if fee > U128(0):
            gold_token = self.storage.get("gold_token")
            contract_addr = self.env.current_contract_address()
            success = self.env.invoke_contract(gold_token, "transfer", [player, contract_addr, fee])
            if not success:
                raise ContractError.TRANSFER_FAILED
            t["prize_pool"] = t["prize_pool"] + fee

        players.push_back(player)
        t["player_count"] = U64(len(players))
        self.storage.set(("tournament", t_id), t)
        self.storage.set(("tournament_players", t_id), players)

        self.env.emit_event("player_registered", {"tournament_id": t_id, "player": player})

        # Auto-start tournament if full
        if t["player_count"] == t["max_players"]:
            self._start_tournament(t, players)

    @external
    def report_match_winner(
        self,
        player: Address,
        t_id: U64,
        round_id: U64,
        match_index: U64,
        reported_winner: Address
    ):
        """Report the winner of a match. If both players agree, it auto-resolves."""
        self._require_initialized()
        player.require_auth()

        match_key = ("match", t_id, round_id, match_index)
        m = self.storage.get(match_key, None)
        if m is None:
            raise ContractError.MATCH_NOT_FOUND
        if m["completed"]:
            raise ContractError.MATCH_COMPLETED

        # Ensure sender is one of the players
        if player != m["player1"] and player != m["player2"]:
            raise ContractError.UNAUTHORIZED

        # Ensure reported winner is one of the players
        if reported_winner != m["player1"] and reported_winner != m["player2"]:
            raise ContractError.UNAUTHORIZED

        if player == m["player1"]:
            m["p1_report"] = reported_winner
        else:
            m["p2_report"] = reported_winner

        # Check if both have reported
        null_addr = Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF")
        if m["p1_report"] != null_addr and m["p2_report"] != null_addr:
            if m["p1_report"] == m["p2_report"]:
                # Consensus
                m["winner"] = m["p1_report"]
                m["completed"] = True
                self.env.emit_event("match_resolved", {
                    "tournament_id": t_id,
                    "round_id": round_id,
                    "match_index": match_index,
                    "winner": m["winner"],
                    "method": Symbol("consensus")
                })
            else:
                # Disagreement - keep open for referee
                self.env.emit_event("match_conflict", {
                    "tournament_id": t_id,
                    "round_id": round_id,
                    "match_index": match_index,
                    "p1_report": m["p1_report"],
                    "p2_report": m["p2_report"]
                })
        
        self.storage.set(match_key, m)

    @external
    def claim_timeout_forfeit(
        self,
        caller: Address,
        t_id: U64,
        round_id: U64,
        match_index: U64
    ):
        """Forfeit the opponent if they haven't reported after the deadline has passed."""
        self._require_initialized()
        caller.require_auth()

        match_key = ("match", t_id, round_id, match_index)
        m = self.storage.get(match_key, None)
        if m is None:
            raise ContractError.MATCH_NOT_FOUND
        if m["completed"]:
            raise ContractError.MATCH_COMPLETED

        now = self.env.ledger().timestamp()
        if now < m["deadline"]:
            raise ContractError.DEADLINE_NOT_PASSED

        # Determine winner: whoever reported, the other forfeits
        null_addr = Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF")
        p1_r = m["p1_report"]
        p2_r = m["p2_report"]

        winner = null_addr
        if p1_r != null_addr and p2_r == null_addr:
            winner = m["player1"]
        elif p2_r != null_addr and p1_r == null_addr:
            winner = m["player2"]
        else:
            # Both failed to report or both have reported but conflict
            # In case of both missing, referee must handle, or disqualify both.
            # We default to disqualifying both (winner is null_addr)
            winner = null_addr

        m["winner"] = winner
        m["completed"] = True
        self.storage.set(match_key, m)

        self.env.emit_event("match_resolved", {
            "tournament_id": t_id,
            "round_id": round_id,
            "match_index": match_index,
            "winner": winner,
            "method": Symbol("timeout_forfeit")
        })

    @external
    def progress_round(self, actor: Address, t_id: U64):
        """Advance the tournament to the next round if all matches in the current round are completed."""
        self._require_initialized()
        actor.require_auth()

        t = self.storage.get(("tournament", t_id), None)
        if t is None:
            raise ContractError.TOURNAMENT_NOT_FOUND
        if t["status"] != U64(TournamentStatus.ACTIVE):
            raise ContractError.INVALID_TOURNAMENT_STATUS

        curr_round = t["current_round"]
        max_players = t["max_players"]
        
        # Total matches in current round: max_players / (2 ^ (round_id + 1))
        matches_count = max_players / (U64(1) << (curr_round + U64(1)))

        winners = Vec()
        for i in range(int(matches_count)):
            m = self.storage.get(("match", t_id, curr_round, U64(i)), None)
            if m is None or not m["completed"]:
                raise ContractError.ROUND_NOT_COMPLETE
            winners.push_back(m["winner"])

        # Check if tournament is completed
        if len(winners) == 1:
            # Winner found! Pay out the prize pool
            grand_winner = winners[0]
            t["status"] = U64(TournamentStatus.COMPLETED)
            self.storage.set(("tournament", t_id), t)

            prize = t["prize_pool"]
            if prize > U128(0):
                gold_token = self.storage.get("gold_token")
                # Deduct 5% admin fee
                admin_fee = (prize * U128(5)) / U128(100)
                winner_share = prize - admin_fee
                contract_addr = self.env.current_contract_address()

                # Pay winner
                success = self.env.invoke_contract(gold_token, "transfer", [contract_addr, grand_winner, winner_share])
                if not success:
                    raise ContractError.TRANSFER_FAILED

                # Pay admin fee
                admin_addr = self.storage.get("admin")
                self.env.invoke_contract(gold_token, "transfer", [contract_addr, admin_addr, admin_fee])

            self.env.emit_event("tournament_completed", {"tournament_id": t_id, "winner": grand_winner, "prize": prize})
            return

        # Initialize matches for the next round
        next_round = curr_round + U64(1)
        next_matches_count = matches_count / U64(2)
        now = self.env.ledger().timestamp()
        deadline = now + t["duration_per_round"]

        null_addr = Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF")

        for i in range(int(next_matches_count)):
            p1 = winners[U64(i * 2)]
            p2 = winners[U64(i * 2 + 1)]

            match_state = {
                "player1": p1,
                "player2": p2,
                "winner": null_addr,
                "p1_report": null_addr,
                "p2_report": null_addr,
                "deadline": deadline,
                "completed": False
            }

            # If either player was disqualified (null_addr), auto-advance the other
            if p1 == null_addr and p2 != null_addr:
                match_state["winner"] = p2
                match_state["completed"] = True
            elif p2 == null_addr and p1 != null_addr:
                match_state["winner"] = p1
                match_state["completed"] = True
            elif p1 == null_addr and p2 == null_addr:
                match_state["winner"] = null_addr
                match_state["completed"] = True

            self.storage.set(("match", t_id, next_round, U64(i)), match_state)

        t["current_round"] = next_round
        t["round_deadline"] = deadline
        self.storage.set(("tournament", t_id), t)

        self.env.emit_event("round_advanced", {"tournament_id": t_id, "new_round": next_round, "deadline": deadline})

    # ------------------------------------------------------------------ #
    #  View Operations                                                    #
    # ------------------------------------------------------------------ #

    @view
    def get_tournament(self, t_id: U64) -> Map:
        """Retrieve details of a tournament."""
        self._require_initialized()
        t = self.storage.get(("tournament", t_id), None)
        if t is None:
            raise ContractError.TOURNAMENT_NOT_FOUND
        return t

    @view
    def get_match(self, t_id: U64, round_id: U64, match_index: U64) -> Map:
        """Retrieve details of a match."""
        self._require_initialized()
        m = self.storage.get(("match", t_id, round_id, match_index), None)
        if m is None:
            raise ContractError.MATCH_NOT_FOUND
        return m

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

    def _require_referee(self, caller: Address):
        admin = self.storage.get("admin")
        if caller == admin:
            return
        if not self.storage.get(("referee", caller), False):
            raise ContractError.UNAUTHORIZED

    def _is_power_of_two(self, n: U64) -> Bool:
        val = int(n)
        return val > 0 and (val & (val - 1)) == 0

    def _start_tournament(self, t: Map, players: Vec):
        """Create initial round 0 bracket matches and mark tournament active."""
        t_id = t["id"]
        t["status"] = U64(TournamentStatus.ACTIVE)
        t["current_round"] = U64(0)
        
        now = self.env.ledger().timestamp()
        deadline = now + t["duration_per_round"]
        t["round_deadline"] = deadline
        self.storage.set(("tournament", t_id), t)

        # Seed players sequentially
        num_matches = t["max_players"] / U64(2)
        null_addr = Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF")

        for i in range(int(num_matches)):
            p1 = players[U64(i * 2)]
            p2 = players[U64(i * 2 + 1)]

            match_state = {
                "player1": p1,
                "player2": p2,
                "winner": null_addr,
                "p1_report": null_addr,
                "p2_report": null_addr,
                "deadline": deadline,
                "completed": False
            }

            self.storage.set(("match", t_id, U64(0), U64(i)), match_state)

        self.env.emit_event("tournament_started", {"tournament_id": t_id, "round_deadline": deadline})
