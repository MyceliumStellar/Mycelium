"""
Dynamic NFT — Dynamic traits and mutation rules engine.

Mycelium Smart Contract for Stellar. Implements NFTs with mutable traits
(level, XP, power, speed), action-based state updates, block-based
pseudo-randomness, cooldown rules, and level caps.
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
    TOKEN_NOT_FOUND = 5
    LEVEL_CAP_REACHED = 6
    INSUFFICIENT_XP = 7
    MUTATION_COOLDOWN = 8
    INVALID_ACTION = 9
    SUPPLY_EXCEEDED = 10

@contract
class DynamicNFT:
    """
    A dynamic NFT contract where assets can evolve over time or through actions.
    Includes rules for experience gain, leveling up, level caps, and pseudo-random bonus stats.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, max_supply: U64, level_cap: U64, xp_per_level: U64):
        """Initialize the dynamic NFT collection settings."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("max_supply", max_supply)
        self.storage.set("level_cap", level_cap)
        self.storage.set("xp_per_level", xp_per_level)
        self.storage.set("next_token_id", U64(1))
        self.storage.set("total_supply", U64(0))
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "max_supply": max_supply,
            "level_cap": level_cap,
            "xp_per_level": xp_per_level
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause contract actions."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    @external
    def mint(self, caller: Address, to: Address) -> U64:
        """Mint a new dynamic NFT with base stats."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()
        self._require_admin(caller)

        next_id = self.storage.get("next_token_id", U64(1))
        max_supply = self.storage.get("max_supply", U64(0))
        if next_id > max_supply:
            raise ContractError.SUPPLY_EXCEEDED

        # Initialize NFT state
        self.storage.set(f"owner_{next_id}", to)
        self.storage.set(f"level_{next_id}", U64(1))
        self.storage.set(f"xp_{next_id}", U64(0))
        self.storage.set(f"power_{next_id}", U64(10))
        self.storage.set(f"speed_{next_id}", U64(10))
        self.storage.set(f"last_action_{next_id}", U64(0))

        # Adjust supplies/balances
        self.storage.set("next_token_id", next_id + U64(1))
        curr_supply = self.storage.get("total_supply", U64(0))
        self.storage.set("total_supply", curr_supply + U64(1))

        owner_bal = self.storage.get(f"balance_{to}", U64(0))
        self.storage.set(f"balance_{to}", owner_bal + U64(1))

        self.env.emit_event("minted", {"token_id": next_id, "to": to})
        self.env.emit_event("traits_mutated", {
            "token_id": next_id,
            "level": U64(1),
            "xp": U64(0),
            "power": U64(10),
            "speed": U64(10)
        })

        return next_id

    @external
    def perform_action(self, caller: Address, token_id: U64, action_type: U64):
        """
        Perform an action (e.g. train, battle, work) to gain XP and mutate stats.
        
        Args:
            caller: Owner of the token.
            token_id: ID of the dynamic NFT.
            action_type: Type of action (1 = Training, 2 = Questing, 3 = Rest).
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        owner = self._get_owner_or_raise(token_id)
        if caller != owner:
            # Check operator approval
            is_approved = self.storage.get(f"approved_{token_id}") == caller
            is_operator = self.storage.get(f"operator_{owner}_{caller}", False)
            if not (is_approved or is_operator):
                raise ContractError.UNAUTHORIZED

        now = self._get_now()
        last_action = self.storage.get(f"last_action_{token_id}", U64(0))
        # 1-minute cooldown between actions to prevent spamming mutations
        if now < last_action + U64(60):
            raise ContractError.MUTATION_COOLDOWN

        level = self.storage.get(f"level_{token_id}", U64(1))
        level_cap = self.storage.get("level_cap", U64(100))

        xp_gained = U64(0)
        power_gained = U64(0)
        speed_gained = U64(0)

        # Action logic
        if action_type == U64(1):  # Training
            xp_gained = U64(150)
            power_gained = self._generate_pseudo_random_stat_gain(token_id, U64(2))
        elif action_type == U64(2):  # Questing
            xp_gained = U64(300)
            speed_gained = self._generate_pseudo_random_stat_gain(token_id, U64(3))
        elif action_type == U64(3):  # Rest
            # Minor power recovery or small passive boost
            xp_gained = U64(50)
        else:
            raise ContractError.INVALID_ACTION

        # Save action time
        self.storage.set(f"last_action_{token_id}", now)

        # Check level cap
        if level >= level_cap:
            # Level is capped, but stats can still slightly increase up to a limit
            self._update_stats_only(token_id, power_gained, speed_gained)
            self.env.emit_event("action_completed_at_cap", {
                "token_id": token_id,
                "power_gained": power_gained,
                "speed_gained": speed_gained
            })
            return

        # XP adjustment & Level up check
        current_xp = self.storage.get(f"xp_{token_id}", U64(0)) + xp_gained
        xp_needed = level * self.storage.get("xp_per_level", U64(1000))

        levels_up = U64(0)
        while current_xp >= xp_needed and level < level_cap:
            current_xp -= xp_needed
            level += U64(1)
            levels_up += U64(1)
            # Re-calculate next level requirement
            xp_needed = level * self.storage.get("xp_per_level", U64(1000))

        # Update traits
        self.storage.set(f"xp_{token_id}", current_xp)
        self.storage.set(f"level_{token_id}", level)

        # Apply stat updates with dynamic growth scaling by levels gained
        power = self.storage.get(f"power_{token_id}", U64(10)) + power_gained + (levels_up * U64(3))
        speed = self.storage.get(f"speed_{token_id}", U64(10)) + speed_gained + (levels_up * U64(3))
        self.storage.set(f"power_{token_id}", power)
        self.storage.set(f"speed_{token_id}", speed)

        self.env.emit_event("action_completed", {
            "token_id": token_id,
            "action_type": action_type,
            "xp_gained": xp_gained,
            "levels_up": levels_up
        })
        self.env.emit_event("traits_mutated", {
            "token_id": token_id,
            "level": level,
            "xp": current_xp,
            "power": power,
            "speed": speed
        })

    @external
    def time_evolve(self, caller: Address, token_id: U64):
        """Passively evolve the NFT based on time elapsed since the last evolution/action."""
        self._require_initialized()
        self._require_not_paused()

        owner = self._get_owner_or_raise(token_id)
        
        now = self._get_now()
        last_action = self.storage.get(f"last_action_{token_id}", U64(0))
        if last_action == U64(0):
            # Fallback if first time evolution is run
            self.storage.set(f"last_action_{token_id}", now)
            return

        elapsed = now - last_action
        # Evolves if at least 1 day (86400 seconds) has elapsed
        if elapsed < U64(86400):
            raise ContractError.MUTATION_COOLDOWN

        # Calculate time increments (e.g. XP gained per hour/day)
        days_passed = elapsed / U64(86400)
        xp_gain = days_passed * U64(100) # 100 XP per idle day
        
        level = self.storage.get(f"level_{token_id}", U64(1))
        level_cap = self.storage.get("level_cap", U64(100))

        self.storage.set(f"last_action_{token_id}", now)

        if level >= level_cap:
            return

        current_xp = self.storage.get(f"xp_{token_id}", U64(0)) + xp_gain
        xp_needed = level * self.storage.get("xp_per_level", U64(1000))

        levels_up = U64(0)
        while current_xp >= xp_needed and level < level_cap:
            current_xp -= xp_needed
            level += U64(1)
            levels_up += U64(1)
            xp_needed = level * self.storage.get("xp_per_level", U64(1000))

        self.storage.set(f"xp_{token_id}", current_xp)
        self.storage.set(f"level_{token_id}", level)

        power = self.storage.get(f"power_{token_id}", U64(10)) + (levels_up * U64(2))
        speed = self.storage.get(f"speed_{token_id}", U64(10)) + (levels_up * U64(2))
        self.storage.set(f"power_{token_id}", power)
        self.storage.set(f"speed_{token_id}", speed)

        self.env.emit_event("traits_mutated", {
            "token_id": token_id,
            "level": level,
            "xp": current_xp,
            "power": power,
            "speed": speed
        })

    @external
    def admin_mutate(self, caller: Address, token_id: U64, stat: U64, new_val: U64):
        """Admin override to manually mutate a token's stat for custom event distribution."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self._get_owner_or_raise(token_id)

        if stat == U64(1):
            self.storage.set(f"level_{token_id}", new_val)
        elif stat == U64(2):
            self.storage.set(f"xp_{token_id}", new_val)
        elif stat == U64(3):
            self.storage.set(f"power_{token_id}", new_val)
        elif stat == U64(4):
            self.storage.set(f"speed_{token_id}", new_val)
        else:
            raise ContractError.INVALID_ACTION

        self.env.emit_event("admin_override_mutated", {
            "token_id": token_id,
            "stat": stat,
            "new_value": new_val
        })

    # --- VIEWS ---

    @view
    def get_traits(self, token_id: U64) -> Map:
        """Get the full suite of dynamic traits for an NFT."""
        self._require_initialized()
        owner = self._get_owner_or_raise(token_id)
        
        traits = Map(self.env)
        traits.set("owner", owner)
        traits.set("level", self.storage.get(f"level_{token_id}", U64(0)))
        traits.set("xp", self.storage.get(f"xp_{token_id}", U64(0)))
        traits.set("power", self.storage.get(f"power_{token_id}", U64(0)))
        traits.set("speed", self.storage.get(f"speed_{token_id}", U64(0)))
        traits.set("last_action", self.storage.get(f"last_action_{token_id}", U64(0)))
        return traits

    @view
    def get_owner(self, token_id: U64) -> Address:
        """Get owner of token."""
        return self._get_owner_or_raise(token_id)

    @view
    def get_level(self, token_id: U64) -> U64:
        """Get level of token."""
        return self.storage.get(f"level_{token_id}", U64(0))

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

    def _get_owner_or_raise(self, token_id: U64) -> Address:
        owner = self.storage.get(f"owner_{token_id}")
        if owner is None:
            raise ContractError.TOKEN_NOT_FOUND
        return owner

    def _get_now(self) -> U64:
        return self.env.ledger_timestamp()

    def _generate_pseudo_random_stat_gain(self, token_id: U64, max_gain: U64) -> U64:
        """Generate a simple pseudo-random value using block time and token parameters."""
        timestamp = self.env.ledger_timestamp()
        # Seed value using combined elements
        seed = timestamp + token_id
        # Simple modulo arithmetic to find a stat boost in range [1, max_gain]
        boost = (seed % max_gain) + U64(1)
        return boost

    def _update_stats_only(self, token_id: U64, power_gained: U64, speed_gained: U64):
        """Update auxiliary stats directly when level cap is reached."""
        power = self.storage.get(f"power_{token_id}", U64(0)) + power_gained
        speed = self.storage.get(f"speed_{token_id}", U64(0)) + speed_gained
        self.storage.set(f"power_{token_id}", power)
        self.storage.set(f"speed_{token_id}", speed)
