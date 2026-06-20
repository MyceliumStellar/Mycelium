"""
Reputation System — Reputation scoring, half-life decay, and negative feedback.

Mycelium Smart Contract for Stellar. Tracks on-chain user reputation scores,
authorizes specific protocols to report actions, logs negative feedback with rate limits,
and calculates mathematical half-life decay dynamically during score queries.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)

class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    PAUSED = 4
    PROTOCOL_NOT_AUTHORIZED = 5
    COOLDOWN_ACTIVE = 6
    INVALID_SCORE = 7

@contract
class ReputationSystem:
    """
    On-chain reputation system. Integrates with partner protocols
    to capture user activity and calculate decayed reputation scores.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, decay_period_sec: U64):
        """
        Initialize the reputation contract.
        
        Args:
            admin: Contract owner.
            decay_period_sec: Period of inactivity leading to 50% decay (e.g. 30 days = 2592000).
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("decay_period", decay_period_sec)
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "decay_period": decay_period_sec
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause reputation changes."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    @external
    def set_protocol_auth(self, caller: Address, protocol: Address, authorized: Bool):
        """Authorize/revoke partner protocol permissions. Admin only."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        self.storage.set(f"protocol_auth_{protocol}", authorized)
        self.env.emit_event("protocol_auth_updated", {"protocol": protocol, "authorized": authorized})

    # --- REPUTATION WRITES ---

    @external
    def record_activity(self, caller: Address, target: Address, points: U128):
        """
        Record a positive user action to increase reputation.
        Only callable by authorized protocols. Applies decay before adding points.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if not self.storage.get(f"protocol_auth_{caller}", False):
            raise ContractError.PROTOCOL_NOT_AUTHORIZED

        # Retrieve current decayed score
        current_score = self._get_decayed_score(target)
        new_score = current_score + points

        # Update user stats
        self.storage.set(f"score_{target}", new_score)
        self.storage.set(f"last_update_{target}", self._get_now())

        self.env.emit_event("reputation_increased", {
            "target": target,
            "protocol": caller,
            "added_points": points,
            "new_score": new_score
        })

    @external
    def report_negative_feedback(self, caller: Address, target: Address, penalty_bps: U64):
        """
        Report negative feedback/slashing for bad behavior.
        Reduces reputation score by a percentage penalty (basis points).
        Rate-limited per reporter-target pair to prevent griefing.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if not self.storage.get(f"protocol_auth_{caller}", False):
            raise ContractError.PROTOCOL_NOT_AUTHORIZED

        if penalty_bps > U64(5000):  # Maximum 50% slash penalty
            raise ContractError.INVALID_SCORE

        now = self._get_now()
        cooldown = self.storage.get(f"cooldown_{caller}_{target}", U64(0))
        if now < cooldown:
            raise ContractError.COOLDOWN_ACTIVE

        # Set 1-day cooldown for feedback reporting between this protocol and target
        self.storage.set(f"cooldown_{caller}_{target}", now + U64(86400))

        # Apply decay first
        current_score = self._get_decayed_score(target)
        
        # Calculate penalty: (score * penalty_bps) / 10000
        penalty = (current_score * U128(penalty_bps)) / U128(10000)
        new_score = current_score - penalty

        self.storage.set(f"score_{target}", new_score)
        self.storage.set(f"last_update_{target}", now)

        self.env.emit_event("reputation_slashed", {
            "target": target,
            "protocol": caller,
            "penalty_amount": penalty,
            "new_score": new_score
        })

    @external
    def admin_override(self, caller: Address, target: Address, new_score: U128):
        """Admin override to manually override reputation values in disputes."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        self.storage.set(f"score_{target}", new_score)
        self.storage.set(f"last_update_{target}", self._get_now())

        self.env.emit_event("reputation_overridden", {
            "target": target,
            "new_score": new_score
        })

    # --- VIEWS (PORTABILITY) ---

    @view
    def get_reputation_score(self, target: Address) -> U128:
        """Returns the decayed reputation score of a user."""
        self._require_initialized()
        return self._get_decayed_score(target)

    @view
    def get_reputation_details(self, target: Address) -> Map:
        """Query detailed score parameters including last action time."""
        res = Map(self.env)
        decayed = self._get_decayed_score(target)
        
        res.set("reputation_score", decayed)
        res.set("raw_score", self.storage.get(f"score_{target}", U128(0)))
        res.set("last_update", self.storage.get(f"last_update_{target}", U64(0)))
        return res

    # --- INTERNAL HELPERS ---

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_not_paused(self):
        if self.storage.get("paused", False):
            raise ContractError.PAUSED

    def _require_admin(self, caller: Address):
        if caller != self.storage.get("admin"):
            raise ContractError.UNAUTHORIZED

    def _get_now(self) -> U64:
        return self.env.ledger_timestamp()

    def _get_decayed_score(self, target: Address) -> U128:
        """
        Dynamically applies half-life decay based on elapsed time.
        Each full decay period divides the reputation score by 2.
        """
        raw_score = self.storage.get(f"score_{target}", U128(0))
        if raw_score == U128(0):
            return U128(0)

        last_update = self.storage.get(f"last_update_{target}", U64(0))
        if last_update == U64(0):
            return raw_score

        now = self._get_now()
        if now <= last_update:
            return raw_score

        elapsed = now - last_update
        decay_period = self.storage.get("decay_period", U64(2592000)) # Default 30 days

        # Compute number of elapsed half-life cycles
        cycles = elapsed / decay_period
        if cycles == U64(0):
            return raw_score

        # Apply decay: divide by 2^cycles (limit to 64 cycles to prevent overflow)
        if cycles > U64(64):
            return U128(0)

        # Division by shifting or power
        divisor = U128(1) << int(cycles)
        decayed_score = raw_score / divisor
        return decayed_score
