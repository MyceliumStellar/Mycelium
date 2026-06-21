"""
Game Item System — Item registry with durability, level requirements, stats variants, and crafting combinatorics.

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
    ITEM_NOT_FOUND = 4
    INSUFFICIENT_LEVEL = 5
    INSUFFICIENT_DURABILITY = 6
    TRANSFER_FAILED = 7
    RECIPE_NOT_FOUND = 8
    INPUT_COUNT_MISMATCH = 9
    INPUT_TYPE_MISMATCH = 10
    NOT_OWNER = 11
    CRAFTING_FAILED = 12


class Rarity:
    COMMON = 0
    UNCOMMON = 1
    RARE = 2
    EPIC = 3
    LEGENDARY = 4


@contract
class GameItemSystem:
    """Game Item System managing game inventory, item attributes, randomized stat distributions,
    and combinatorial crafting rules."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, gold_token: Address):
        """Initialize the item system."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("gold_token", gold_token)
        self.storage.set("item_count", U64(0))
        self.storage.set("recipe_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin, "gold_token": gold_token})

    # ------------------------------------------------------------------ #
    #  Admin Operations                                                   #
    # ------------------------------------------------------------------ #

    @external
    def register_recipe(
        self,
        admin: Address,
        recipe_id: U64,
        input_types: Vec,
        output_type: Symbol,
        output_rarity: U64,
        min_level: U64,
        success_rate_bps: U64,
        cost: U128,
    ):
        """Register a new crafting recipe. Only Admin."""
        self._require_admin(admin)

        recipe = {
            "id": recipe_id,
            "input_types": input_types,
            "output_type": output_type,
            "output_rarity": output_rarity,
            "min_level": min_level,
            "success_rate_bps": success_rate_bps,
            "cost": cost,
        }

        self.storage.set(("recipe", recipe_id), recipe)
        self.env.emit_event("recipe_registered", {
            "recipe_id": recipe_id,
            "output_type": output_type,
            "output_rarity": output_rarity,
        })

    @external
    def mint_item(
        self,
        admin: Address,
        recipient: Address,
        item_type: Symbol,
        rarity: U64,
        level_req: U64,
        base_attack: U64,
        base_defense: U64,
    ) -> U64:
        """Mint a custom item to a player. Only Admin."""
        self._require_admin(admin)
        return self._mint_item_internal(recipient, item_type, rarity, level_req, base_attack, base_defense)

    # ------------------------------------------------------------------ #
    #  Player Actions                                                     #
    # ------------------------------------------------------------------ #

    @external
    def craft(self, player: Address, recipe_id: U64, input_item_ids: Vec) -> U64:
        """Craft a new item combining input items.

        Args:
            player: The crafting player.
            recipe_id: Target recipe.
            input_item_ids: Vector of unique item IDs to consume.
        """
        self._require_initialized()
        player.require_auth()

        recipe = self.storage.get(("recipe", recipe_id), None)
        if recipe is None:
            raise ContractError.RECIPE_NOT_FOUND

        input_types = recipe["input_types"]
        if len(input_item_ids) != len(input_types):
            raise ContractError.INPUT_COUNT_MISMATCH

        # Check inputs ownership & types match
        for i in range(len(input_item_ids)):
            item_id = input_item_ids[i]
            item = self.storage.get(("item", item_id), None)
            if item is None:
                raise ContractError.ITEM_NOT_FOUND
            if item["owner"] != player:
                raise ContractError.NOT_OWNER
            if item["item_type"] != input_types[i]:
                raise ContractError.INPUT_TYPE_MISMATCH
            if item["durability"] == U64(0):
                raise ContractError.INSUFFICIENT_DURABILITY

        # Charge gold cost
        cost = recipe["cost"]
        if cost > U128(0):
            gold_token = self.storage.get("gold_token")
            contract_addr = self.env.current_contract_address()
            success = self.env.invoke_contract(gold_token, "transfer", [player, contract_addr, cost])
            if not success:
                raise ContractError.TRANSFER_FAILED

        # Burn inputs (by marking owner as zero or removing)
        # We delete from storage
        for i in range(len(input_item_ids)):
            self.storage.set(("item", input_item_ids[i]), None)

        # Success probability roll (pseudo-random using block time/seed)
        now = self.env.ledger().timestamp()
        # Create a basic hash of time + input ids to get a number 0..9999
        entropy_hash = self.env.crypto().keccak256(now, recipe_id, input_item_ids)
        # Parse last byte as random source
        random_roll = U64(int(entropy_hash[31])) * U64(39) % U64(10000)

        success_rate = recipe["success_rate_bps"]
        if random_roll > success_rate:
            # Crafting failed
            self.env.emit_event("crafting_failed", {
                "player": player,
                "recipe_id": recipe_id,
            })
            raise ContractError.CRAFTING_FAILED

        # Determine stats variants based on rarity
        rarity = recipe["output_rarity"]
        # Basic stat variance: base attack = 10, defense = 10
        # Multipliers based on random roll
        stat_multiplier = U64(100) + (random_roll % U64(50)) # 100% to 150%
        if rarity == Rarity.UNCOMMON:
            stat_multiplier = stat_multiplier + U64(20)
        elif rarity == Rarity.RARE:
            stat_multiplier = stat_multiplier + U64(50)
        elif rarity == Rarity.EPIC:
            stat_multiplier = stat_multiplier + U64(100)
        elif rarity == Rarity.LEGENDARY:
            stat_multiplier = stat_multiplier + U64(250)

        base_atk = (U64(15) * stat_multiplier) / U64(100)
        base_def = (U64(10) * stat_multiplier) / U64(100)

        output_id = self._mint_item_internal(
            player,
            recipe["output_type"],
            rarity,
            recipe["min_level"],
            base_atk,
            base_def
        )

        self.env.emit_event("item_crafted", {
            "player": player,
            "recipe_id": recipe_id,
            "item_id": output_id,
            "rarity": rarity,
        })

        return output_id

    @external
    def repair_item(self, player: Address, item_id: U64):
        """Repair a damaged item using gold. Restores durability to 100.

        Args:
            player: Owner of the item.
            item_id: Item to repair.
        """
        self._require_initialized()
        player.require_auth()

        item = self.storage.get(("item", item_id), None)
        if item is None:
            raise ContractError.ITEM_NOT_FOUND
        if item["owner"] != player:
            raise ContractError.NOT_OWNER

        cur_dur = item["durability"]
        if cur_dur >= U64(100):
            return

        damage = U64(100) - cur_dur
        # Cost: 1 gold per 2 durability damage, scaled by rarity
        rarity_factor = U128(item["rarity"] + U64(1))
        repair_cost = U128(damage) * U128(10) * rarity_factor

        gold_token = self.storage.get("gold_token")
        contract_addr = self.env.current_contract_address()
        success = self.env.invoke_contract(gold_token, "transfer", [player, contract_addr, repair_cost])
        if not success:
            raise ContractError.TRANSFER_FAILED

        item["durability"] = U64(100)
        self.storage.set(("item", item_id), item)

        self.env.emit_event("item_repaired", {
            "player": player,
            "item_id": item_id,
            "cost": repair_cost,
        })

    @external
    def use_item(self, reporter: Address, item_id: U64, usage_loss: U64):
        """Apply wear and tear to an item. Called by battle systems/authorized games.

        Args:
            reporter: Game contract.
            item_id: Item used.
            usage_loss: Durability points lost.
        """
        self._require_initialized()
        reporter.require_auth()
        self._require_authorized_game(reporter)

        item = self.storage.get(("item", item_id), None)
        if item is None:
            raise ContractError.ITEM_NOT_FOUND

        cur_dur = item["durability"]
        if cur_dur > usage_loss:
            item["durability"] = cur_dur - usage_loss
        else:
            item["durability"] = U64(0)

        self.storage.set(("item", item_id), item)

        self.env.emit_event("item_durability_lost", {
            "item_id": item_id,
            "new_durability": item["durability"],
        })

    # ------------------------------------------------------------------ #
    #  Admin Configs                                                     #
    # ------------------------------------------------------------------ #

    @external
    def set_authorized_game(self, admin: Address, game_contract: Address, status: Bool):
        """Authorize a game system (e.g. Arena) to consume durability. Only Admin."""
        self._require_admin(admin)
        self.storage.set(("game_auth", game_contract), status)
        self.env.emit_event("game_auth_updated", {"game": game_contract, "status": status})

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
    def get_item(self, item_id: U64) -> Map:
        """Get details of an item."""
        self._require_initialized()
        item = self.storage.get(("item", item_id), None)
        if item is None:
            raise ContractError.ITEM_NOT_FOUND
        return item

    @view
    def get_recipe(self, recipe_id: U64) -> Map:
        """Get details of a recipe."""
        self._require_initialized()
        recipe = self.storage.get(("recipe", recipe_id), None)
        if recipe is None:
            raise ContractError.RECIPE_NOT_FOUND
        return recipe

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

    def _require_authorized_game(self, caller: Address):
        admin = self.storage.get("admin")
        if caller == admin:
            return
        if not self.storage.get(("game_auth", caller), False):
            raise ContractError.UNAUTHORIZED

    def _mint_item_internal(
        self,
        recipient: Address,
        item_type: Symbol,
        rarity: U64,
        level_req: U64,
        attack: U64,
        defense: U64,
    ) -> U64:
        item_id = self.storage.get("item_count", U64(0)) + U64(1)
        self.storage.set("item_count", item_id)

        item = {
            "id": item_id,
            "owner": recipient,
            "item_type": item_type,
            "rarity": rarity,
            "durability": U64(100),
            "level_req": level_req,
            "attack": attack,
            "defense": defense,
        }

        self.storage.set(("item", item_id), item)

        self.env.emit_event("item_minted", {
            "recipient": recipient,
            "item_id": item_id,
            "item_type": item_type,
            "rarity": rarity,
        })

        return item_id
