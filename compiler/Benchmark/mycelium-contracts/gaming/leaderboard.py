"""
Leaderboard System — Seasonal multi-category top-N scoring, score decay, and anti-cheat tracking.

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
    INVALID_MAX_INCREMENT = 5
    SCORE_TOO_LOW = 6
    CHEAT_CHECK_FAILED = 7
    SEASON_NOT_ACTIVE = 8
    INVALID_TOP_N_LIMIT = 9
    REWARDS_ALREADY_PAID = 10
    NO_REWARDS_CONFIGURED = 11


@contract
class LeaderboardSystem:
    """Manages secure gaming leaderboards with top-N tracking, seasonal rewards, and decay mechanisms."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        payout_token: Address,
        decay_factor_bps: U64,
        top_n_limit: U64
    ):
        """Initialize the Leaderboard system with default global parameters."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        if top_n_limit == U64(0) or top_n_limit > U64(100):
            raise ContractError.INVALID_TOP_N_LIMIT

        self.storage.set("admin", admin)
        self.storage.set("payout_token", payout_token)
        self.storage.set("decay_factor_bps", decay_factor_bps)
        self.storage.set("top_n_limit", top_n_limit)
        self.storage.set("active_season", U64(1))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "payout_token": payout_token,
            "decay_factor": decay_factor_bps,
            "top_n_limit": top_n_limit
        })

    # ------------------------------------------------------------------ #
    #  Admin Configurations                                               #
    # ------------------------------------------------------------------ #

    @external
    def set_reporter(self, admin: Address, reporter: Address, status: Bool):
        """Authorize or revoke a game contract/server to submit scores. Only Admin."""
        self._require_admin(admin)
        self.storage.set(("reporter", reporter), status)
        self.env.emit_event("reporter_status_updated", {"reporter": reporter, "status": status})

    @external
    def register_category(
        self,
        admin: Address,
        category: Symbol,
        max_score_increment: U64,
        min_submit_interval_sec: U64
    ):
        """Configure anti-cheat restrictions for a leaderboard category. Only Admin."""
        self._require_admin(admin)
        if max_score_increment == U64(0):
            raise ContractError.INVALID_MAX_INCREMENT

        self.storage.set(("category_max_increment", category), max_score_increment)
        self.storage.set(("category_interval", category), min_submit_interval_sec)

        self.env.emit_event("category_registered", {
            "category": category,
            "max_increment": max_score_increment,
            "min_interval": min_submit_interval_sec
        })

    @external
    def set_rewards_share(self, admin: Address, rank_shares: Vec):
        """Set payout splits for top-N players (in bps, e.g. [5000, 3000, 2000]). Only Admin."""
        self._require_admin(admin)
        
        # Verify total shares don't exceed 10000 (100%)
        total = U64(0)
        for i in range(len(rank_shares)):
            total = total + rank_shares[i]

        if total > U64(10000):
            raise ContractError.CHEAT_CHECK_FAILED

        self.storage.set("rewards_share", rank_shares)
        self.env.emit_event("rewards_share_updated", {"shares": rank_shares})

    @external
    def trigger_season_transition(self, admin: Address, reward_pool_amount: U128):
        """Close current season, pay out rewards to top-N, decay scores, and start next season. Only Admin."""
        self._require_admin(admin)
        
        current_season = self.storage.get("active_season")
        next_season = current_season + U64(1)

        # We pay out reward for all active categories configured on this contract
        # For simplicity, we fetch top rewards shares
        shares = self.storage.get("rewards_share", None)
        payout_token = self.storage.get("payout_token")
        contract_addr = self.env.current_contract_address()

        # Mark rewards paid to prevent double payouts if this function is called multiple times
        if self.storage.get(("season_paid", current_season), False):
            raise ContractError.REWARDS_ALREADY_PAID

        # Distribute rewards if pool amount is non-zero and rewards configured
        if reward_pool_amount > U128(0) and shares is not None and len(shares) > 0:
            # Let's say we have standard categories registered. We'll check active categories from storage
            # We fetch categories list
            categories = self.storage.get("registered_categories", Vec())
            if len(categories) > 0:
                # Divide reward pool equally among categories
                reward_per_category = reward_pool_amount / U128(len(categories))
                for c in range(len(categories)):
                    category = categories[c]
                    board = self.storage.get(("board", current_season, category), Vec())
                    
                    # Distribute category pool to top players based on shares
                    for i in range(len(board)):
                        if i >= len(shares):
                            break
                        player = board[i]["player"]
                        share_bps = shares[i]
                        player_reward = (reward_per_category * U128(share_bps)) / U128(10000)
                        
                        if player_reward > U128(0):
                            success = self.env.invoke_contract(payout_token, "transfer", [contract_addr, player, player_reward])
                            if not success:
                                raise ContractError.TRANSFER_FAILED
                            
                            self.env.emit_event("season_reward_paid", {
                                "season": current_season,
                                "category": category,
                                "player": player,
                                "rank": U64(i + 1),
                                "amount": player_reward
                            })

        self.storage.set(("season_paid", current_season), True)

        # Decay mechanism: transfer active player scores to new season with decay applied
        # In a blockchain context, we can't iterate all players, but we can decay the scores
        # of the top-N list to seed the new season, or decay upon their next submission.
        # We will decay the top-N lists for next season automatically
        categories = self.storage.get("registered_categories", Vec())
        decay_factor = self.storage.get("decay_factor_bps")

        for c in range(len(categories)):
            category = categories[c]
            old_board = self.storage.get(("board", current_season, category), Vec())
            new_board = Vec()
            for i in range(len(old_board)):
                player = old_board[i]["player"]
                old_score = old_board[i]["score"]
                decayed_score = (old_score * decay_factor) / U64(10000)
                
                if decayed_score > U64(0):
                    # Seed new season scores for top players
                    self.storage.set(("score", next_season, category, player), decayed_score)
                    
                    entry = Map()
                    entry.set(Symbol("player"), player)
                    entry.set(Symbol("score"), decayed_score)
                    entry.set(Symbol("timestamp"), self.env.ledger().timestamp())
                    new_board.push_back(entry)
            
            # Sort new board and set
            self.storage.set(("board", next_season, category), new_board)

        # Update active season index
        self.storage.set("active_season", next_season)
        self.env.emit_event("season_transitioned", {"from": current_season, "to": next_season})

    # ------------------------------------------------------------------ #
    #  Player / Game Operations                                           #
    # ------------------------------------------------------------------ #

    @external
    def submit_score(
        self,
        reporter: Address,
        player: Address,
        category: Symbol,
        score_increment: U64
    ) -> U64:
        """Submit a score increment for a player in a specific category. Must be authorized reporter."""
        self._require_initialized()
        reporter.require_auth()
        self._require_reporter(reporter)

        season = self.storage.get("active_season")

        # Anti-cheat checks
        max_inc = self.storage.get(("category_max_increment", category), None)
        if max_inc is None:
            raise ContractError.CHEAT_CHECK_FAILED

        if score_increment > max_inc:
            raise ContractError.CHEAT_CHECK_FAILED

        # Check time interval for rate limits
        now = self.env.ledger().timestamp()
        last_submit = self.storage.get(("last_submit", season, category, player), U64(0))
        min_interval = self.storage.get(("category_interval", category), U64(0))
        if now - last_submit < min_interval:
            raise ContractError.CHEAT_CHECK_FAILED

        # Track category globally if new
        categories = self.storage.get("registered_categories", Vec())
        has_cat = False
        for i in range(len(categories)):
            if categories[i] == category:
                has_cat = True
                break
        if not has_cat:
            categories.push_back(category)
            self.storage.set("registered_categories", categories)

        # Retrieve current score
        curr_score = self.storage.get(("score", season, category, player), U64(0))
        new_score = curr_score + score_increment

        # Update player storage
        self.storage.set(("score", season, category, player), new_score)
        self.storage.set(("last_submit", season, category, player), now)

        # Update top-N leaderboard board
        self._update_board_insert(season, category, player, new_score, now)

        self.env.emit_event("score_submitted", {
            "season": season,
            "category": category,
            "player": player,
            "increment": score_increment,
            "new_score": new_score
        })

        return new_score

    # ------------------------------------------------------------------ #
    #  View Operations                                                    #
    # ------------------------------------------------------------------ #

    @view
    def get_leaderboard(self, season: U64, category: Symbol) -> Vec:
        """Retrieve the top-N board for a given season and category."""
        self._require_initialized()
        return self.storage.get(("board", season, category), Vec())

    @view
    def get_player_score(self, season: U64, category: Symbol, player: Address) -> U64:
        """Retrieve current cumulative score of a player."""
        self._require_initialized()
        return self.storage.get(("score", season, category, player), U64(0))

    @view
    def get_config(self) -> Map:
        """Get global parameters of the leaderboard system."""
        self._require_initialized()
        config = Map()
        config.set(Symbol("admin"), self.storage.get("admin"))
        config.set(Symbol("payout_token"), self.storage.get("payout_token"))
        config.set(Symbol("decay_factor"), self.storage.get("decay_factor_bps"))
        config.set(Symbol("top_n_limit"), self.storage.get("top_n_limit"))
        config.set(Symbol("active_season"), self.storage.get("active_season"))
        return config

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

    def _require_reporter(self, caller: Address):
        admin = self.storage.get("admin")
        if caller == admin:
            return
        if not self.storage.get(("reporter", caller), False):
            raise ContractError.UNAUTHORIZED

    def _update_board_insert(self, season: U64, category: Symbol, player: Address, new_score: U64, timestamp: U64):
        """Insert a player into the category leaderboard sorted descending."""
        board = self.storage.get(("board", season, category), Vec())
        limit = self.storage.get("top_n_limit")

        # Find if player already exists in the board, remove them if they do
        new_board = Vec()
        for i in range(len(board)):
            entry = board[i]
            if entry["player"] != player:
                new_board.push_back(entry)

        # Create new entry
        new_entry = Map()
        new_entry.set(Symbol("player"), player)
        new_entry.set(Symbol("score"), new_score)
        new_entry.set(Symbol("timestamp"), timestamp)

        # Insert at the sorted position
        inserted = False
        sorted_board = Vec()
        for i in range(len(new_board)):
            entry = new_board[i]
            if not inserted and new_score > entry["score"]:
                sorted_board.push_back(new_entry)
                inserted = True
            sorted_board.push_back(entry)

        if not inserted:
            sorted_board.push_back(new_entry)

        # Trim to top-N limit
        final_board = Vec()
        for i in range(len(sorted_board)):
            if i >= limit:
                break
            final_board.push_back(sorted_board[i])

        self.storage.set(("board", season, category), final_board)
