"""
Guild System — Treasury budgets, invitation controls, rank hierarchies, guild challenges, and level scaling.

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
    GUILD_NAME_TAKEN = 5
    ALREADY_IN_GUILD = 6
    NOT_IN_GUILD = 7
    INSUFFICIENT_RANK = 8
    GUILD_NOT_FOUND = 9
    MEMBER_LIMIT_REACHED = 10
    NO_INVITATION = 11
    INSUFFICIENT_TREASURY = 12
    CHALLENGE_NOT_FOUND = 13
    INVALID_CHALLENGE_STATUS = 14
    SAME_GUILD_CHALLENGE = 15
    INVALID_TREASURY_BUDGET = 16


class Ranks:
    MEMBER = 1
    ELDER = 2
    OFFICER = 3
    LEADER = 4


class ChallengeStatus:
    PENDING = 1
    ACCEPTED = 2
    REJECTED = 3
    COMPLETED = 4
    CANCELLED = 5


@contract
class GuildSystem:
    """Manages guild creation, role ranks, treasury budgets, member invites, and GvG match staking."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, gold_token: Address, creation_fee: U128):
        """Initialize the Guild system."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("gold_token", gold_token)
        self.storage.set("creation_fee", creation_fee)
        self.storage.set("guild_count", U64(0))
        self.storage.set("challenge_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "gold_token": gold_token,
            "creation_fee": creation_fee
        })

    # ------------------------------------------------------------------ #
    #  Player Actions                                                     #
    # ------------------------------------------------------------------ #

    @external
    def create_guild(self, creator: Address, name: Symbol, open_to_join: Bool) -> U64:
        """Create a new guild. Charges a creation fee in gold tokens."""
        self._require_initialized()
        creator.require_auth()

        # Ensure creator is not already in a guild
        if self.storage.get(("member_guild", creator), U64(0)) != U64(0):
            raise ContractError.ALREADY_IN_GUILD

        # Check if name is taken
        if self.storage.get(("name_taken", name), False):
            raise ContractError.GUILD_NAME_TAKEN

        # Pay creation fee
        fee = self.storage.get("creation_fee")
        if fee > U128(0):
            gold_token = self.storage.get("gold_token")
            contract_addr = self.env.current_contract_address()
            success = self.env.invoke_contract(gold_token, "transfer", [creator, contract_addr, fee])
            if not success:
                raise ContractError.TRANSFER_FAILED

        guild_id = self.storage.get("guild_count") + U64(1)
        self.storage.set("guild_count", guild_id)

        # Initialize guild details
        guild = {
            "id": guild_id,
            "name": name,
            "leader": creator,
            "level": U64(1),
            "xp": U64(0),
            "treasury": U128(0),
            "open_to_join": open_to_join,
            "member_count": U64(1)
        }

        self.storage.set(("guild", guild_id), guild)
        self.storage.set(("name_taken", name), True)
        self.storage.set(("member_guild", creator), guild_id)
        self.storage.set(("member_rank", creator), U64(Ranks.LEADER))

        self.env.emit_event("guild_created", {
            "guild_id": guild_id,
            "name": name,
            "leader": creator
        })

        return guild_id

    @external
    def invite_player(self, inviter: Address, invitee: Address):
        """Invite a player to the guild. Requires Officer rank or higher."""
        self._require_initialized()
        inviter.require_auth()

        guild_id = self.storage.get(("member_guild", inviter), U64(0))
        if guild_id == U64(0):
            raise ContractError.NOT_IN_GUILD

        rank = self.storage.get(("member_rank", inviter), U64(0))
        if rank < U64(Ranks.OFFICER):
            raise ContractError.INSUFFICIENT_RANK

        # Check if invitee is already in a guild
        if self.storage.get(("member_guild", invitee), U64(0)) != U64(0):
            raise ContractError.ALREADY_IN_GUILD

        self.storage.set(("invite", guild_id, invitee), True)
        self.env.emit_event("player_invited", {"guild_id": guild_id, "invitee": invitee, "inviter": inviter})

    @external
    def accept_invite(self, player: Address, guild_id: U64):
        """Accept an invitation to join a guild."""
        self._require_initialized()
        player.require_auth()

        if self.storage.get(("member_guild", player), U64(0)) != U64(0):
            raise ContractError.ALREADY_IN_GUILD

        guild = self.storage.get(("guild", guild_id), None)
        if guild is None:
            raise ContractError.GUILD_NOT_FOUND

        # Validate max members limit based on level: limit = level * 10 + 10
        max_members = guild["level"] * U64(10) + U64(10)
        if guild["member_count"] >= max_members:
            raise ContractError.MEMBER_LIMIT_REACHED

        # If invite-only, check for invite
        if not guild["open_to_join"]:
            if not self.storage.get(("invite", guild_id, player), False):
                raise ContractError.NO_INVITATION
            # Clear invite
            self.storage.set(("invite", guild_id, player), False)

        # Add member to guild
        self.storage.set(("member_guild", player), guild_id)
        self.storage.set(("member_rank", player), U64(Ranks.MEMBER))

        guild["member_count"] = guild["member_count"] + U64(1)
        self.storage.set(("guild", guild_id), guild)

        self.env.emit_event("member_joined", {"guild_id": guild_id, "member": player})

    @external
    def promote_member(self, actor: Address, target: Address):
        """Promote a member's rank. Only leader or higher officer than target can promote."""
        self._require_initialized()
        actor.require_auth()

        guild_id = self.storage.get(("member_guild", actor), U64(0))
        target_guild = self.storage.get(("member_guild", target), U64(0))

        if guild_id == U64(0) or guild_id != target_guild:
            raise ContractError.UNAUTHORIZED

        actor_rank = self.storage.get(("member_rank", actor), U64(0))
        target_rank = self.storage.get(("member_rank", target), U64(0))

        if actor_rank <= target_rank or actor_rank < U64(Ranks.OFFICER):
            raise ContractError.INSUFFICIENT_RANK

        new_rank = target_rank + U64(1)
        if new_rank >= U64(Ranks.LEADER):
            # To pass leadership, actor must explicitly step down
            raise ContractError.UNAUTHORIZED

        self.storage.set(("member_rank", target), new_rank)
        self.env.emit_event("member_promoted", {"guild_id": guild_id, "member": target, "new_rank": new_rank})

    @external
    def leave_guild(self, player: Address):
        """Leave current guild. Leaders cannot leave without transferring leadership first."""
        self._require_initialized()
        player.require_auth()

        guild_id = self.storage.get(("member_guild", player), U64(0))
        if guild_id == U64(0):
            raise ContractError.NOT_IN_GUILD

        rank = self.storage.get(("member_rank", player), U64(0))
        guild = self.storage.get(("guild", guild_id), None)
        if guild is None:
            raise ContractError.GUILD_NOT_FOUND

        if rank == U64(Ranks.LEADER) and guild["member_count"] > U64(1):
            raise ContractError.UNAUTHORIZED

        # Remove member
        self.storage.set(("member_guild", player), U64(0))
        self.storage.set(("member_rank", player), U64(0))

        guild["member_count"] = guild["member_count"] - U64(1)
        if guild["member_count"] == U64(0):
            # Disband guild
            self.storage.set(("guild", guild_id), None)
            self.storage.set(("name_taken", guild["name"]), False)
        else:
            self.storage.set(("guild", guild_id), guild)

        self.env.emit_event("member_left", {"guild_id": guild_id, "member": player})

    # ------------------------------------------------------------------ #
    #  Treasury & Budgets                                                 #
    # ------------------------------------------------------------------ #

    @external
    def deposit_treasury(self, depositor: Address, amount: U128):
        """Deposit gold tokens into the guild treasury."""
        self._require_initialized()
        depositor.require_auth()

        guild_id = self.storage.get(("member_guild", depositor), U64(0))
        if guild_id == U64(0):
            raise ContractError.NOT_IN_GUILD

        guild = self.storage.get(("guild", guild_id), None)
        if guild is None:
            raise ContractError.GUILD_NOT_FOUND

        gold_token = self.storage.get("gold_token")
        contract_addr = self.env.current_contract_address()
        success = self.env.invoke_contract(gold_token, "transfer", [depositor, contract_addr, amount])
        if not success:
            raise ContractError.TRANSFER_FAILED

        # Update treasury
        guild["treasury"] = guild["treasury"] + amount
        
        # Award guild XP for deposits (e.g. 1 XP per 100 gold, maxed out)
        xp_gain = U64(amount / U128(100))
        self._add_guild_xp(guild, xp_gain)

        self.storage.set(("guild", guild_id), guild)
        self.env.emit_event("treasury_deposit", {"guild_id": guild_id, "depositor": depositor, "amount": amount})

    @external
    def withdraw_treasury(self, actor: Address, recipient: Address, amount: U128):
        """Withdraw gold tokens from guild treasury. Officer/Leader only, within budget limits."""
        self._require_initialized()
        actor.require_auth()

        guild_id = self.storage.get(("member_guild", actor), U64(0))
        if guild_id == U64(0):
            raise ContractError.NOT_IN_GUILD

        rank = self.storage.get(("member_rank", actor), U64(0))
        if rank < U64(Ranks.OFFICER):
            raise ContractError.INSUFFICIENT_RANK

        guild = self.storage.get(("guild", guild_id), None)
        if guild is None:
            raise ContractError.GUILD_NOT_FOUND

        if guild["treasury"] < amount:
            raise ContractError.INSUFFICIENT_TREASURY

        # If Officer, check daily/weekly budget limit (e.g., officer limit = guild_level * 500 gold tokens)
        if rank == U64(Ranks.OFFICER):
            limit = U128(guild["level"]) * U128(500)
            withdrawn = self.storage.get(("officer_withdrawn", guild_id, actor), U128(0))
            if withdrawn + amount > limit:
                raise ContractError.INVALID_TREASURY_BUDGET
            self.storage.set(("officer_withdrawn", guild_id, actor), withdrawn + amount)

        # Process withdrawal
        guild["treasury"] = guild["treasury"] - amount
        self.storage.set(("guild", guild_id), guild)

        gold_token = self.storage.get("gold_token")
        success = self.env.invoke_contract(gold_token, "transfer", [self.env.current_contract_address(), recipient, amount])
        if not success:
            raise ContractError.TRANSFER_FAILED

        self.env.emit_event("treasury_withdraw", {
            "guild_id": guild_id,
            "actor": actor,
            "recipient": recipient,
            "amount": amount
        })

    # ------------------------------------------------------------------ #
    #  Guild vs Guild (GvG) Challenges                                   #
    # ------------------------------------------------------------------ #

    @external
    def challenge_guild(self, actor: Address, target_guild_id: U64, wager: U128) -> U64:
        """Challenge another guild, locking wager from treasury. Leader/Officer only."""
        self._require_initialized()
        actor.require_auth()

        guild_id = self.storage.get(("member_guild", actor), U64(0))
        if guild_id == U64(0) or guild_id == target_guild_id:
            raise ContractError.SAME_GUILD_CHALLENGE

        rank = self.storage.get(("member_rank", actor), U64(0))
        if rank < U64(Ranks.OFFICER):
            raise ContractError.INSUFFICIENT_RANK

        guild = self.storage.get(("guild", guild_id), None)
        target = self.storage.get(("guild", target_guild_id), None)
        if guild is None or target is None:
            raise ContractError.GUILD_NOT_FOUND

        if guild["treasury"] < wager:
            raise ContractError.INSUFFICIENT_TREASURY

        # Lock wager
        guild["treasury"] = guild["treasury"] - wager
        self.storage.set(("guild", guild_id), guild)

        challenge_id = self.storage.get("challenge_count") + U64(1)
        self.storage.set("challenge_count", challenge_id)

        challenge = {
            "id": challenge_id,
            "challenger": guild_id,
            "target": target_guild_id,
            "wager": wager,
            "status": U64(ChallengeStatus.PENDING),
            "timestamp": self.env.ledger().timestamp()
        }

        self.storage.set(("challenge", challenge_id), challenge)
        self.env.emit_event("gvg_challenged", {
            "challenge_id": challenge_id,
            "challenger": guild_id,
            "target": target_guild_id,
            "wager": wager
        })

        return challenge_id

    @external
    def respond_challenge(self, actor: Address, challenge_id: U64, accept: Bool):
        """Accept or decline GvG challenge. If accepted, target locks equal wager amount."""
        self._require_initialized()
        actor.require_auth()

        challenge = self.storage.get(("challenge", challenge_id), None)
        if challenge is None:
            raise ContractError.CHALLENGE_NOT_FOUND

        if challenge["status"] != U64(ChallengeStatus.PENDING):
            raise ContractError.INVALID_CHALLENGE_STATUS

        guild_id = self.storage.get(("member_guild", actor), U64(0))
        if guild_id != challenge["target"]:
            raise ContractError.UNAUTHORIZED

        rank = self.storage.get(("member_rank", actor), U64(0))
        if rank < U64(Ranks.OFFICER):
            raise ContractError.INSUFFICIENT_RANK

        if accept:
            target_guild = self.storage.get(("guild", guild_id), None)
            if target_guild is None:
                raise ContractError.GUILD_NOT_FOUND

            wager = challenge["wager"]
            if target_guild["treasury"] < wager:
                raise ContractError.INSUFFICIENT_TREASURY

            target_guild["treasury"] = target_guild["treasury"] - wager
            self.storage.set(("guild", guild_id), target_guild)

            challenge["status"] = U64(ChallengeStatus.ACCEPTED)
        else:
            challenge["status"] = U64(ChallengeStatus.REJECTED)
            # Refund challenger wager
            challenger_id = challenge["challenger"]
            challenger_guild = self.storage.get(("guild", challenger_id), None)
            if challenger_guild is not None:
                challenger_guild["treasury"] = challenger_guild["treasury"] + challenge["wager"]
                self.storage.set(("guild", challenger_id), challenger_guild)

        self.storage.set(("challenge", challenge_id), challenge)
        self.env.emit_event("gvg_response", {"challenge_id": challenge_id, "accepted": accept})

    @external
    def resolve_challenge(self, reporter: Address, challenge_id: U64, winner_id: U64):
        """Resolve a GvG challenge. Distributes both wagers to winner's treasury. Authorized referee only."""
        self._require_initialized()
        reporter.require_auth()
        self._require_referee(reporter)

        challenge = self.storage.get(("challenge", challenge_id), None)
        if challenge is None:
            raise ContractError.CHALLENGE_NOT_FOUND

        if challenge["status"] != U64(ChallengeStatus.ACCEPTED):
            raise ContractError.INVALID_CHALLENGE_STATUS

        challenger_id = challenge["challenger"]
        target_id = challenge["target"]

        if winner_id != challenger_id and winner_id != target_id:
            raise ContractError.UNAUTHORIZED

        winner_guild = self.storage.get(("guild", winner_id), None)
        if winner_guild is None:
            raise ContractError.GUILD_NOT_FOUND

        # Winner gets total wager
        total_payout = challenge["wager"] * U128(2)
        winner_guild["treasury"] = winner_guild["treasury"] + total_payout
        
        # Award GvG XP
        self._add_guild_xp(winner_guild, U64(500))
        self.storage.set(("guild", winner_id), winner_guild)

        challenge["status"] = U64(ChallengeStatus.COMPLETED)
        self.storage.set(("challenge", challenge_id), challenge)

        self.env.emit_event("gvg_resolved", {
            "challenge_id": challenge_id,
            "winner_guild": winner_id,
            "total_payout": total_payout
        })

    # ------------------------------------------------------------------ #
    #  Admin Configs                                                     #
    # ------------------------------------------------------------------ #

    @external
    def set_referee(self, admin: Address, referee: Address, status: Bool):
        """Set authorized match resolving referee contract. Only Admin."""
        self._require_admin(admin)
        self.storage.set(("referee", referee), status)
        self.env.emit_event("referee_status_updated", {"referee": referee, "status": status})

    # ------------------------------------------------------------------ #
    #  View Operations                                                    #
    # ------------------------------------------------------------------ #

    @view
    def get_guild(self, guild_id: U64) -> Map:
        """Get guild info."""
        self._require_initialized()
        guild = self.storage.get(("guild", guild_id), None)
        if guild is None:
            raise ContractError.GUILD_NOT_FOUND
        return guild

    @view
    def get_player_guild(self, player: Address) -> Map:
        """Get guild information of a player."""
        self._require_initialized()
        guild_id = self.storage.get(("member_guild", player), U64(0))
        if guild_id == U64(0):
            raise ContractError.NOT_IN_GUILD
        
        guild = self.storage.get(("guild", guild_id), None)
        if guild is None:
            raise ContractError.GUILD_NOT_FOUND

        rank = self.storage.get(("member_rank", player), U64(0))
        res = Map()
        res.set(Symbol("guild_id"), guild_id)
        res.set(Symbol("rank"), rank)
        res.set(Symbol("level"), guild["level"])
        res.set(Symbol("name"), guild["name"])
        return res

    @view
    def get_challenge(self, challenge_id: U64) -> Map:
        """Get GvG challenge details."""
        self._require_initialized()
        challenge = self.storage.get(("challenge", challenge_id), None)
        if challenge is None:
            raise ContractError.CHALLENGE_NOT_FOUND
        return challenge

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

    def _add_guild_xp(self, guild: Map, amount: U64):
        """Add XP to a guild and scale up its level if threshold is met."""
        new_xp = guild["xp"] + amount
        curr_level = guild["level"]

        # Level up threshold: level * 1000 XP
        threshold = curr_level * U64(1000)
        while new_xp >= threshold:
            new_xp = new_xp - threshold
            curr_level = curr_level + U64(1)
            threshold = curr_level * U64(1000)

        guild["xp"] = new_xp
        guild["level"] = curr_level
