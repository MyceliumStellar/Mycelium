"""
Sports Oracle — Sports game result oracle with staking, disputes, and arbitration.

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
    INSUFFICIENT_STAKE = 4
    GAME_NOT_FOUND = 5
    INVALID_STATE = 6
    DISPUTE_PERIOD_EXPIRED = 7
    DISPUTE_PERIOD_ACTIVE = 8
    TRANSFER_FAILED = 9
    ALREADY_REPORTED = 10
    REENTRANT_CALL = 11


class GameStatus:
    CREATED = 0
    REPORTED = 1
    DISPUTED = 2
    RESOLVED = 3


@contract
class SportsOracle:
    """Sports Oracle contract managing match registries, staked reporter submissions,
    public dispute periods, and arbitrator resolutions."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        stake_token: Address,
        arbitrator: Address,
        min_stake: U128,
        dispute_bond: U128,
        dispute_period: U64,
        min_reports: U64,
    ):
        """Initialize configurations."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("stake_token", stake_token)
        self.storage.set("arbitrator", arbitrator)
        self.storage.set("min_stake", min_stake)
        self.storage.set("dispute_bond", dispute_bond)
        self.storage.set("dispute_period", dispute_period)
        self.storage.set("min_reports", min_reports)
        
        self.storage.set("execution_lock", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "arbitrator": arbitrator,
            "stake_token": stake_token,
            "dispute_period": dispute_period,
        })

    # ------------------------------------------------------------------ #
    #  Reporter Staking                                                   #
    # ------------------------------------------------------------------ #

    @external
    def register_reporter(self, reporter: Address, stake_amount: U128):
        """Register as a reporter by depositing stake."""
        self._require_initialized()
        reporter.require_auth()

        min_stake = self.storage.get("min_stake")
        current_stake = self.storage.get(("stake", reporter), U128(0))
        total_stake = current_stake + stake_amount

        if total_stake < min_stake:
            raise ContractError.INSUFFICIENT_STAKE

        stake_token = self.storage.get("stake_token")
        contract_addr = self.env.current_contract_address()
        success = self.env.invoke_contract(stake_token, "transfer", [reporter, contract_addr, stake_amount])
        if not success:
            raise ContractError.TRANSFER_FAILED

        self.storage.set(("stake", reporter), total_stake)
        self.storage.set(("reporter_active", reporter), True)

        self.env.emit_event("reporter_registered", {
            "reporter": reporter,
            "added_stake": stake_amount,
            "total_stake": total_stake,
        })

    @external
    def withdraw_stake(self, reporter: Address, amount: U128):
        """Withdraw reporter stake."""
        self._require_initialized()
        reporter.require_auth()

        current_stake = self.storage.get(("stake", reporter), U128(0))
        if current_stake < amount:
            raise ContractError.INSUFFICIENT_STAKE

        new_stake = current_stake - amount
        min_stake = self.storage.get("min_stake")

        if new_stake < min_stake:
            self.storage.set(("reporter_active", reporter), False)

        self.storage.set(("stake", reporter), new_stake)

        stake_token = self.storage.get("stake_token")
        contract_addr = self.env.current_contract_address()
        success = self.env.invoke_contract(stake_token, "transfer", [contract_addr, reporter, amount])
        if not success:
            raise ContractError.TRANSFER_FAILED

        self.env.emit_event("stake_withdrawn", {
            "reporter": reporter,
            "amount": amount,
            "remaining_stake": new_stake,
        })

    # ------------------------------------------------------------------ #
    #  Game Operations                                                     #
    # ------------------------------------------------------------------ #

    @external
    def create_game(
        self,
        creator: Address,
        game_id: U64,
        home_team: Symbol,
        away_team: Symbol,
        start_time: U64,
    ):
        """Register a new sports event. Only Admin."""
        self._require_initialized()
        self._require_admin(creator)

        existing = self.storage.get(("game", game_id), None)
        if existing is not None:
            raise ContractError.INVALID_STATE

        game = {
            "id": game_id,
            "home_team": home_team,
            "away_team": away_team,
            "start_time": start_time,
            "home_score": U32(0),
            "away_score": U32(0),
            "status": GameStatus.CREATED,
            "report_deadline": U64(0),
            "disputer": creator, # placeholder
            "dispute_deadline": U64(0),
        }

        self.storage.set(("game", game_id), game)
        self.storage.set(("game_reporters", game_id), Vec())

        self.env.emit_event("game_created", {
            "game_id": game_id,
            "home_team": home_team,
            "away_team": away_team,
            "start_time": start_time,
        })

    @external
    def report_result(
        self,
        reporter: Address,
        game_id: U64,
        home_score: U32,
        away_score: U32,
    ):
        """Submit a result for a registered game.

        Args:
            reporter: Staked reporter.
            game_id: Target game.
            home_score: Final home team score.
            away_score: Final away team score.
        """
        self._require_initialized()
        reporter.require_auth()

        if not self.storage.get(("reporter_active", reporter), False):
            raise ContractError.UNAUTHORIZED

        game = self.storage.get(("game", game_id), None)
        if game is None:
            raise ContractError.GAME_NOT_FOUND

        if game["status"] != GameStatus.CREATED:
            raise ContractError.INVALID_STATE

        # Prevent double report by same reporter
        has_reported = self.storage.get(("reported", game_id, reporter), False)
        if has_reported:
            raise ContractError.ALREADY_REPORTED

        now = self.env.ledger().timestamp()
        if now < game["start_time"]:
            raise ContractError.INVALID_STATE

        # Save reporter submission
        submission = {
            "home_score": home_score,
            "away_score": away_score,
            "reporter": reporter,
        }
        self.storage.set(("submission", game_id, reporter), submission)
        self.storage.set(("reported", game_id, reporter), True)

        reporters_vec = self.storage.get(("game_reporters", game_id), Vec())
        reporters_vec.append(reporter)
        self.storage.set(("game_reporters", game_id), reporters_vec)

        # Count score agreements
        # We need `min_reports` agreement to set the status to REPORTED
        min_reports = self.storage.get("min_reports")
        
        agreement_count = U64(0)
        for i in range(len(reporters_vec)):
            rep = reporters_vec[i]
            rep_sub = self.storage.get(("submission", game_id, rep))
            if rep_sub["home_score"] == home_score and rep_sub["away_score"] == away_score:
                agreement_count = agreement_count + U64(1)

        if agreement_count >= min_reports:
            dispute_period = self.storage.get("dispute_period")
            game["status"] = GameStatus.REPORTED
            game["home_score"] = home_score
            game["away_score"] = away_score
            game["dispute_deadline"] = now + dispute_period
            
            self.storage.set(("game", game_id), game)

            self.env.emit_event("game_reported", {
                "game_id": game_id,
                "home_score": home_score,
                "away_score": away_score,
                "dispute_deadline": game["dispute_deadline"],
            })

    @external
    def dispute_result(self, disputer: Address, game_id: U64):
        """Dispute the reported score by posting a dispute bond.

        Args:
            disputer: Address raising the dispute.
            game_id: The disputed game.
        """
        self._require_initialized()
        disputer.require_auth()

        game = self.storage.get(("game", game_id), None)
        if game is None:
            raise ContractError.GAME_NOT_FOUND

        if game["status"] != GameStatus.REPORTED:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        if now > game["dispute_deadline"]:
            raise ContractError.DISPUTE_PERIOD_EXPIRED

        # Charge dispute bond
        bond = self.storage.get("dispute_bond")
        stake_token = self.storage.get("stake_token")
        contract_addr = self.env.current_contract_address()
        success = self.env.invoke_contract(stake_token, "transfer", [disputer, contract_addr, bond])
        if not success:
            raise ContractError.TRANSFER_FAILED

        game["status"] = GameStatus.DISPUTED
        game["disputer"] = disputer
        
        self.storage.set(("game", game_id), game)

        self.env.emit_event("game_disputed", {
            "game_id": game_id,
            "disputer": disputer,
            "dispute_bond": bond,
        })

    @external
    def resolve_arbitration(
        self,
        arbitrator: Address,
        game_id: U64,
        correct_home_score: U32,
        correct_away_score: U32,
    ):
        """Resolve a disputed game result. Only designated Arbitrator.

        Args:
            arbitrator: Arbitrator address.
            game_id: Disputed game.
            correct_home_score: Evaluated home score.
            correct_away_score: Evaluated away score.
        """
        self._require_initialized()
        arbitrator.require_auth()
        self._require_arbitrator(arbitrator)
        self._require_no_reentrant()

        game = self.storage.get(("game", game_id), None)
        if game is None:
            raise ContractError.GAME_NOT_FOUND

        if game["status"] != GameStatus.DISPUTED:
            raise ContractError.INVALID_STATE

        original_home = game["home_score"]
        original_away = game["away_score"]
        
        disputer = game["disputer"]
        dispute_bond = self.storage.get("dispute_bond")
        stake_token = self.storage.get("stake_token")
        contract_addr = self.env.current_contract_address()

        is_dispute_valid = (original_home != correct_home_score) or (original_away != correct_away_score)

        if is_dispute_valid:
            # Disputer was correct!
            # Refund disputer's bond
            self.env.invoke_contract(stake_token, "transfer", [contract_addr, disputer, dispute_bond])
            
            # Slash original matching reporters
            reporters_vec = self.storage.get(("game_reporters", game_id))
            slashed_total = U128(0)
            
            for i in range(len(reporters_vec)):
                rep = reporters_vec[i]
                sub = self.storage.get(("submission", game_id, rep))
                if sub["home_score"] == original_home and sub["away_score"] == original_away:
                    # Slash this reporter
                    rep_stake = self.storage.get(("stake", rep), U128(0))
                    slash_amount = self.storage.get("min_stake") # slash their active reporting stake
                    if rep_stake < slash_amount:
                        slash_amount = rep_stake
                    
                    self.storage.set(("stake", rep), rep_stake - slash_amount)
                    if (rep_stake - slash_amount) < self.storage.get("min_stake"):
                        self.storage.set(("reporter_active", rep), False)
                        
                    slashed_total = slashed_total + slash_amount
            
            # Give slashed rewards to disputer as bounty
            if slashed_total > U128(0):
                self.env.invoke_contract(stake_token, "transfer", [contract_addr, disputer, slashed_total])
        else:
            # Disputer was wrong, reporters were correct!
            # Slash disputer's bond. Give it to the correct reporters.
            reporters_vec = self.storage.get(("game_reporters", game_id))
            correct_reporters = Vec()
            
            for i in range(len(reporters_vec)):
                rep = reporters_vec[i]
                sub = self.storage.get(("submission", game_id, rep))
                if sub["home_score"] == original_home and sub["away_score"] == original_away:
                    correct_reporters.append(rep)
            
            correct_count = len(correct_reporters)
            if correct_count > 0 and dispute_bond > U128(0):
                share = dispute_bond / U128(correct_count)
                for i in range(correct_count):
                    rep = correct_reporters[i]
                    rep_stake = self.storage.get(("stake", rep), U128(0))
                    self.storage.set(("stake", rep), rep_stake + share)

        # Update final score
        game["home_score"] = correct_home_score
        game["away_score"] = correct_away_score
        game["status"] = GameStatus.RESOLVED
        self.storage.set(("game", game_id), game)

        self.env.emit_event("arbitration_resolved", {
            "game_id": game_id,
            "home_score": correct_home_score,
            "away_score": correct_away_score,
            "dispute_upheld": is_dispute_valid,
        })

    @external
    def finalize_result(self, caller: Address, game_id: U64):
        """Finalize game if the dispute period has passed without challenge.

        Args:
            caller: Any address.
            game_id: Game to finalize.
        """
        self._require_initialized()
        caller.require_auth()

        game = self.storage.get(("game", game_id), None)
        if game is None:
            raise ContractError.GAME_NOT_FOUND

        if game["status"] != GameStatus.REPORTED:
            raise ContractError.INVALID_STATE

        now = self.env.ledger().timestamp()
        if now <= game["dispute_deadline"]:
            raise ContractError.DISPUTE_PERIOD_ACTIVE

        game["status"] = GameStatus.RESOLVED
        self.storage.set(("game", game_id), game)

        self.env.emit_event("game_finalized", {
            "game_id": game_id,
            "home_score": game["home_score"],
            "away_score": game["away_score"],
        })

    # ------------------------------------------------------------------ #
    #  Admin Parameters                                                    #
    # ------------------------------------------------------------------ #

    @external
    def update_config(
        self,
        admin: Address,
        arbitrator: Address,
        dispute_bond: U128,
        dispute_period: U64,
        min_reports: U64,
    ):
        """Update configurations. Only Admin."""
        self._require_admin(admin)
        self.storage.set("arbitrator", arbitrator)
        self.storage.set("dispute_bond", dispute_bond)
        self.storage.set("dispute_period", dispute_period)
        self.storage.set("min_reports", min_reports)
        self.env.emit_event("config_updated", {
            "arbitrator": arbitrator,
            "dispute_bond": dispute_bond,
            "dispute_period": dispute_period,
        })

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
    def get_game(self, game_id: U64) -> Map:
        """Get game details."""
        self._require_initialized()
        game = self.storage.get(("game", game_id), None)
        if game is None:
            raise ContractError.GAME_NOT_FOUND
        return game

    @view
    def get_reporter_stake(self, reporter: Address) -> U128:
        """Get reporter stake amount."""
        self._require_initialized()
        return self.storage.get(("stake", reporter), U128(0))

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

    def _require_arbitrator(self, caller: Address):
        arb = self.storage.get("arbitrator")
        if caller != arb:
            raise ContractError.UNAUTHORIZED

    def _require_no_reentrant(self):
        if self.storage.get("execution_lock", False):
            raise ContractError.REENTRANT_CALL
