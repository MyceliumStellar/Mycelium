"""
Battle Arena — Turn-based duel matchmaking with ELO updates, speed turn order, status conditions, and duel payouts.

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
    PLAYER_NOT_REGISTERED = 4
    DUEL_NOT_FOUND = 5
    INVALID_STATE = 6
    TRANSFER_FAILED = 7
    NOT_PLAYER_TURN = 8
    ABILITY_ON_COOLDOWN = 9
    DUEL_ALREADY_RESOLVED = 10
    SELF_CHALLENGE = 11
    INSUFFICIENT_FUNDS = 12


class DuelStatus:
    PENDING = 0
    ACTIVE = 1
    RESOLVED = 2
    CANCELLED = 3


class StatusEffect:
    NONE = 0
    POISON = 1
    BURN = 2
    STUN = 3


# Config Constants
INITIAL_ELO = U64(1000)
K_FACTOR = U64(32)
TREASURY_FEE_BPS = U64(500) # 5%


@contract
class BattleArena:
    """Battle Arena contract managing player profiles, ELO matchmaking,
    turn scheduling, ability cooldowns, status debuffs, and escrow payouts."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, gold_token: Address, treasury: Address):
        """Initialize the arena contract."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("gold_token", gold_token)
        self.storage.set("treasury", treasury)
        self.storage.set("duel_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin, "gold_token": gold_token})

    # ------------------------------------------------------------------ #
    #  Player Registration                                                #
    # ------------------------------------------------------------------ #

    @external
    def register_player(self, player: Address, speed: U64, attack: U64, defense: U64):
        """Register player stats in the Arena database.

        Args:
            player: Address of the player.
            speed: Base speed (determines turn order).
            attack: Base attack power.
            defense: Base defense power.
        """
        self._require_initialized()
        player.require_auth()

        if self.storage.get(("player_registered", player), False):
            raise ContractError.INVALID_STATE

        # Cap base stats sum to prevent over-powered characters
        if speed + attack + defense > U64(150):
            raise ContractError.INVALID_STATE

        self.storage.set(("player_registered", player), True)
        self.storage.set(("elo", player), INITIAL_ELO)
        self.storage.set(("speed", player), speed)
        self.storage.set(("attack", player), attack)
        self.storage.set(("defense", player), defense)

        self.env.emit_event("player_registered", {
            "player": player,
            "elo": INITIAL_ELO,
            "speed": speed,
        })

    # ------------------------------------------------------------------ #
    #  Duel Management                                                    #
    # ------------------------------------------------------------------ #

    @external
    def challenge(self, challenger: Address, opponent: Address, entry_fee: U128) -> U64:
        """Challenge another player to a duel. Locks entry fee.

        Args:
            challenger: Address proposing the duel.
            opponent: Target player address.
            entry_fee: Stake amount in game gold.
        """
        self._require_initialized()
        challenger.require_auth()

        if challenger == opponent:
            raise ContractError.SELF_CHALLENGE
        if not self.storage.get(("player_registered", challenger), False):
            raise ContractError.PLAYER_NOT_REGISTERED
        if not self.storage.get(("player_registered", opponent), False):
            raise ContractError.PLAYER_NOT_REGISTERED

        # Charge entry fee
        gold_token = self.storage.get("gold_token")
        contract_addr = self.env.current_contract_address()
        if entry_fee > U128(0):
            success = self.env.invoke_contract(gold_token, "transfer", [challenger, contract_addr, entry_fee])
            if not success:
                raise ContractError.TRANSFER_FAILED

        duel_id = self.storage.get("duel_count", U64(0)) + U64(1)
        self.storage.set("duel_count", duel_id)

        duel = {
            "id": duel_id,
            "challenger": challenger,
            "opponent": opponent,
            "entry_fee": entry_fee,
            "status": DuelStatus.PENDING,
            "winner": challenger, # placeholder
            
            # Battle state trackers
            "challenger_hp": U64(100),
            "opponent_hp": U64(100),
            "current_turn_player": challenger, # speed resolved during accept
            "turn_number": U64(1),
            
            # Ability cooldowns
            "challenger_cooldown_1": U64(0),
            "opponent_cooldown_1": U64(0),
            
            # Status conditions
            "challenger_status": U64(StatusEffect.NONE),
            "challenger_status_dur": U64(0),
            "opponent_status": U64(StatusEffect.NONE),
            "opponent_status_dur": U64(0),
        }

        self.storage.set(("duel", duel_id), duel)

        self.env.emit_event("duel_challenged", {
            "duel_id": duel_id,
            "challenger": challenger,
            "opponent": opponent,
            "entry_fee": entry_fee,
        })

        return duel_id

    @external
    def accept_challenge(self, opponent: Address, duel_id: U64):
        """Accept challenge and deposit entry fee, resolving initial turn order by speed.

        Args:
            opponent: Opponent accepting the challenge.
            duel_id: Target duel.
        """
        self._require_initialized()
        opponent.require_auth()

        duel = self.storage.get(("duel", duel_id), None)
        if duel is None:
            raise ContractError.DUEL_NOT_FOUND

        if duel["opponent"] != opponent:
            raise ContractError.UNAUTHORIZED
        if duel["status"] != DuelStatus.PENDING:
            raise ContractError.INVALID_STATE

        # Lock entry fee
        entry_fee = duel["entry_fee"]
        if entry_fee > U128(0):
            gold_token = self.storage.get("gold_token")
            contract_addr = self.env.current_contract_address()
            success = self.env.invoke_contract(gold_token, "transfer", [opponent, contract_addr, entry_fee])
            if not success:
                raise ContractError.TRANSFER_FAILED

        # Resolve initial turn order based on Speed
        challenger = duel["challenger"]
        c_speed = self.storage.get(("speed", challenger), U64(50))
        o_speed = self.storage.get(("speed", opponent), U64(50))

        if o_speed > c_speed:
            duel["current_turn_player"] = opponent
        else:
            duel["current_turn_player"] = challenger

        duel["status"] = DuelStatus.ACTIVE
        self.storage.set(("duel", duel_id), duel)

        self.env.emit_event("duel_started", {
            "duel_id": duel_id,
            "first_attacker": duel["current_turn_player"],
        })

    # ------------------------------------------------------------------ #
    #  Combat Gameplay                                                     #
    # ------------------------------------------------------------------ #

    @external
    def play_action(self, player: Address, duel_id: U64, action_type: U64):
        """Execute character action for current turn.

        Args:
            player: Acting player.
            duel_id: Active duel.
            action_type: 1 = Basic Strike, 2 = Special Skill (inflicts status, cooldown=2), 3 = Defend.
        """
        self._require_initialized()
        player.require_auth()

        duel = self.storage.get(("duel", duel_id), None)
        if duel is None:
            raise ContractError.DUEL_NOT_FOUND
        if duel["status"] != DuelStatus.ACTIVE:
            raise ContractError.INVALID_STATE
        if duel["current_turn_player"] != player:
            raise ContractError.NOT_PLAYER_TURN

        is_challenger = (player == duel["challenger"])
        opponent = duel["opponent"] if is_challenger else duel["challenger"]

        # 1. Apply status damage or checks on self before action
        self._apply_statuses_pre_turn(duel, is_challenger)

        # Check if stun skipped action
        my_status = duel["challenger_status"] if is_challenger else duel["opponent_status"]
        if my_status == StatusEffect.STUN:
            # Skip action, clear stun
            if is_challenger:
                duel["challenger_status"] = U64(StatusEffect.NONE)
                duel["challenger_status_dur"] = U64(0)
            else:
                duel["opponent_status"] = U64(StatusEffect.NONE)
                duel["opponent_status_dur"] = U64(0)
            
            # Switch turn
            duel["current_turn_player"] = opponent
            duel["turn_number"] = duel["turn_number"] + U64(1)
            self.storage.set(("duel", duel_id), duel)
            
            self.env.emit_event("action_stunned", {"duel_id": duel_id, "player": player})
            return

        # Decrement own cooldowns
        if is_challenger:
            if duel["challenger_cooldown_1"] > U64(0):
                duel["challenger_cooldown_1"] = duel["challenger_cooldown_1"] - U64(1)
        else:
            if duel["opponent_cooldown_1"] > U64(0):
                duel["opponent_cooldown_1"] = duel["opponent_cooldown_1"] - U64(1)

        # 2. Execute Action
        my_atk = self.storage.get(("attack", player), U64(10))
        opp_def = self.storage.get(("defense", opponent), U64(10))
        
        damage = U64(0)
        status_to_inflict = U64(StatusEffect.NONE)

        if action_type == U64(1):
            # Basic Strike: simple damage calculation
            damage = my_atk
            if damage > opp_def / U64(2):
                damage = damage - opp_def / U64(2)
            else:
                damage = U64(2) # minimum strike damage
        elif action_type == U64(2):
            # Special Ability (cooldown 2): deals extra damage and inflicts POISON or BURN
            cooldown = duel["challenger_cooldown_1"] if is_challenger else duel["opponent_cooldown_1"]
            if cooldown > U64(0):
                raise ContractError.ABILITY_ON_COOLDOWN

            damage = my_atk + U64(5)
            # Roll for status
            now = self.env.ledger().timestamp()
            if now % U64(2) == U64(0):
                status_to_inflict = U64(StatusEffect.POISON)
            else:
                status_to_inflict = U64(StatusEffect.BURN)

            # Set cooldown
            if is_challenger:
                duel["challenger_cooldown_1"] = U64(3) # 3 turns cooldown
            else:
                duel["opponent_cooldown_1"] = U64(3)
        elif action_type == U64(3):
            # Defend: heal self slightly or prepare shield
            heal = U64(5)
            if is_challenger:
                duel["challenger_hp"] = min(U64(100), duel["challenger_hp"] + heal)
            else:
                duel["opponent_hp"] = min(U64(100), duel["opponent_hp"] + heal)
        else:
            raise ContractError.INVALID_STATE

        # Apply damage to opponent
        if damage > U64(0):
            if is_challenger:
                if duel["opponent_hp"] > damage:
                    duel["opponent_hp"] = duel["opponent_hp"] - damage
                else:
                    duel["opponent_hp"] = U64(0)
            else:
                if duel["challenger_hp"] > damage:
                    duel["challenger_hp"] = duel["challenger_hp"] - damage
                else:
                    duel["challenger_hp"] = U64(0)

        # Inflict status
        if status_to_inflict != StatusEffect.NONE:
            if is_challenger:
                duel["opponent_status"] = status_to_inflict
                duel["opponent_status_dur"] = U64(2) # lasts 2 turns
            else:
                duel["challenger_status"] = status_to_inflict
                duel["challenger_status_dur"] = U64(2)

        # Check death / victory condition
        c_hp = duel["challenger_hp"]
        o_hp = duel["opponent_hp"]

        if c_hp == U64(0) or o_hp == U64(0):
            # Resolve match!
            winner = opponent if c_hp == U64(0) else player
            self._resolve_duel_payout(duel, winner)
        else:
            # Cycle turn
            duel["current_turn_player"] = opponent
            duel["turn_number"] = duel["turn_number"] + U64(1)
            self.storage.set(("duel", duel_id), duel)

        self.env.emit_event("action_played", {
            "duel_id": duel_id,
            "player": player,
            "action_type": action_type,
            "damage": damage,
            "challenger_hp": duel["challenger_hp"],
            "opponent_hp": duel["opponent_hp"],
        })

    # ------------------------------------------------------------------ #
    #  Internal Combat Helpers                                            #
    # ------------------------------------------------------------------ #

    def _apply_statuses_pre_turn(self, duel: Map, is_challenger: Bool):
        """Calculate and apply status damage to active player before action."""
        status = duel["challenger_status"] if is_challenger else duel["opponent_status"]
        dur = duel["challenger_status_dur"] if is_challenger else duel["opponent_status_dur"]

        if dur == U64(0) or status == StatusEffect.NONE:
            return

        damage = U64(0)

        if status == StatusEffect.POISON:
            damage = U64(5) # ticks 5 damage
        elif status == StatusEffect.BURN:
            damage = U64(8) # ticks 8 damage

        if is_challenger:
            if duel["challenger_hp"] > damage:
                duel["challenger_hp"] = duel["challenger_hp"] - damage
            else:
                duel["challenger_hp"] = U64(0)
            duel["challenger_status_dur"] = dur - U64(1)
            if duel["challenger_status_dur"] == U64(0):
                duel["challenger_status"] = U64(StatusEffect.NONE)
        else:
            if duel["opponent_hp"] > damage:
                duel["opponent_hp"] = duel["opponent_hp"] - damage
            else:
                duel["opponent_hp"] = U64(0)
            duel["opponent_status_dur"] = dur - U64(1)
            if duel["opponent_status_dur"] == U64(0):
                duel["opponent_status"] = U64(StatusEffect.NONE)

    def _resolve_duel_payout(self, duel: Map, winner: Address):
        """Distribute funds to the winner, take treasury fee, and recalculate ELO ratings."""
        duel["status"] = DuelStatus.RESOLVED
        duel["winner"] = winner
        
        self.storage.set(("duel", duel["id"]), duel)

        challenger = duel["challenger"]
        opponent = duel["opponent"]
        entry_fee = duel["entry_fee"]

        # Recalculate ELO
        c_elo = self.storage.get(("elo", challenger), INITIAL_ELO)
        o_elo = self.storage.get(("elo", opponent), INITIAL_ELO)

        # Simplified ELO calculation
        # Max ELO delta of 32
        winner_is_c = (winner == challenger)
        
        elo_diff = c_elo - o_elo if c_elo > o_elo else o_elo - c_elo
        change = U64(16)
        if elo_diff < U64(400):
            change = U64(16) + (elo_diff / U64(25))

        if winner_is_c:
            self.storage.set(("elo", challenger), c_elo + change)
            if o_elo > change:
                self.storage.set(("elo", opponent), o_elo - change)
            else:
                self.storage.set(("elo", opponent), U64(100)) # Floor ELO
        else:
            self.storage.set(("elo", opponent), o_elo + change)
            if c_elo > change:
                self.storage.set(("elo", challenger), c_elo - change)
            else:
                self.storage.set(("elo", challenger), U64(100))

        # Prize Payout
        total_prize = entry_fee * U128(2)
        if total_prize > U128(0):
            fee = (total_prize * U128(TREASURY_FEE_BPS)) / U128(10000)
            net_prize = total_prize - fee
            
            gold_token = self.storage.get("gold_token")
            treasury = self.storage.get("treasury")
            contract_addr = self.env.current_contract_address()

            # Transfer fee to treasury
            if fee > U128(0):
                self.env.invoke_contract(gold_token, "transfer", [contract_addr, treasury, fee])
            # Transfer prize to winner
            self.env.invoke_contract(gold_token, "transfer", [contract_addr, winner, net_prize])

        self.env.emit_event("duel_resolved", {
            "duel_id": duel["id"],
            "winner": winner,
            "new_challenger_elo": self.storage.get(("elo", challenger)),
            "new_opponent_elo": self.storage.get(("elo", opponent)),
        })

    # ------------------------------------------------------------------ #
    #  Admin Operations                                                   #
    # ------------------------------------------------------------------ #

    @external
    def cancel_duel(self, admin: Address, duel_id: U64):
        """Cancel a pending duel and return entry fees. Only Admin."""
        self._require_admin(admin)
        duel = self.storage.get(("duel", duel_id), None)
        if duel is None:
            raise ContractError.DUEL_NOT_FOUND
        if duel["status"] != DuelStatus.PENDING:
            raise ContractError.INVALID_STATE

        duel["status"] = DuelStatus.CANCELLED
        self.storage.set(("duel", duel_id), duel)

        # Refund challenger
        entry_fee = duel["entry_fee"]
        if entry_fee > U128(0):
            gold_token = self.storage.get("gold_token")
            contract_addr = self.env.current_contract_address()
            self.env.invoke_contract(gold_token, "transfer", [contract_addr, duel["challenger"], entry_fee])

        self.env.emit_event("duel_cancelled", {"duel_id": duel_id})

    @external
    def transfer_admin(self, admin: Address, new_admin: Address):
        """Transfer admin. Only Admin."""
        self._require_admin(admin)
        self.storage.set("admin", new_admin)
        self.env.emit_event("admin_transferred", {"old_admin": admin, "new_admin": new_admin})

    # ------------------------------------------------------------------ #
    #  View Functions                                                     #
    # ------------------------------------------------------------------ #

    @view
    def get_player_stats(self, player: Address) -> Map:
        """Retrieve registered player arena attributes."""
        self._require_initialized()
        return {
            "elo": self.storage.get(("elo", player), U64(0)),
            "speed": self.storage.get(("speed", player), U64(0)),
            "attack": self.storage.get(("attack", player), U64(0)),
            "defense": self.storage.get(("defense", player), U64(0)),
        }

    @view
    def get_duel(self, duel_id: U64) -> Map:
        """Get duel information."""
        self._require_initialized()
        duel = self.storage.get(("duel", duel_id), None)
        if duel is None:
            raise ContractError.DUEL_NOT_FOUND
        return duel

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
