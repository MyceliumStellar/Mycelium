"""
Land Registry System — 2D coordinate grid, building placements, neighbor bonuses, taxes, and rental leases.

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
    LAND_ALREADY_OWNED = 5
    LAND_NOT_OWNER = 6
    LAND_NOT_FOUND = 7
    TAX_OVERDUE = 8
    RENT_ACTIVE = 9
    RENT_NOT_ACTIVE = 10
    INVALID_RENT_PRICE = 11
    BUILDING_NOT_ALLOWED = 12
    TAX_PAYMENT_FAILED = 13


@contract
class LandRegistrySystem:
    """Manages virtual land grid coordinates, tax collections, building additions, and leasing agreements."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, gold_token: Address, base_price: U128):
        """Initialize the Land Registry contract."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("gold_token", gold_token)
        self.storage.set("base_land_price", base_price)
        self.storage.set("tax_interval_sec", U64(86400 * 7)) # 1 week
        self.storage.set("tax_price_per_week", U128(100)) # 100 gold
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "gold_token": gold_token,
            "base_price": base_price
        })

    # ------------------------------------------------------------------ #
    #  Player Actions                                                     #
    # ------------------------------------------------------------------ #

    @external
    def claim_land(self, player: Address, x: I128, y: I128) -> Bool:
        """Buy a coordinate grid parcel if it's currently unowned."""
        self._require_initialized()
        player.require_auth()

        # Check if land is already claimed
        owner = self.storage.get(("land_owner", x, y), None)
        if owner is not None:
            raise ContractError.LAND_ALREADY_OWNED

        # Calculate coordinate price (closer to (0,0) might be more expensive)
        base_price = self.storage.get("base_land_price")
        dist = self._abs_val(x) + self._abs_val(y)
        # Price decay further from center, minimum is 50% of base
        price_modifier = U128(100)
        if dist > I128(10):
            price_modifier = U128(50)
        
        purchase_cost = (base_price * price_modifier) / U128(100)

        # Pay purchase cost
        gold_token = self.storage.get("gold_token")
        contract_addr = self.env.current_contract_address()
        success = self.env.invoke_contract(gold_token, "transfer", [player, contract_addr, purchase_cost])
        if not success:
            raise ContractError.TRANSFER_FAILED

        # Record land details
        now = self.env.ledger().timestamp()
        land = {
            "x": x,
            "y": y,
            "owner": player,
            "building_type": Symbol("none"),
            "building_level": U64(0),
            "last_tax_payment": now,
            "renter": Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"), # Null address placeholder
            "rent_expires": U64(0),
            "rent_price_per_sec": U128(0)
        }

        self.storage.set(("land", x, y), land)
        self.storage.set(("land_owner", x, y), player)

        self.env.emit_event("land_claimed", {
            "owner": player,
            "x": x,
            "y": y,
            "price": purchase_cost
        })

        return True

    @external
    def place_building(self, player: Address, x: I128, y: I128, building_type: Symbol):
        """Place or upgrade a building on owned or rented land. Updates neighbor bonuses."""
        self._require_initialized()
        player.require_auth()

        land = self.storage.get(("land", x, y), None)
        if land is None:
            raise ContractError.LAND_NOT_FOUND

        now = self.env.ledger().timestamp()
        
        # Check taxes first (must be paid up)
        if now - land["last_tax_payment"] > self.storage.get("tax_interval_sec") * U64(2):
            raise ContractError.TAX_OVERDUE

        # Verify permission: either owner (if not rented) or renter (if lease is active)
        is_renter = False
        if land["renter"] != Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF") and land["rent_expires"] > now:
            if land["renter"] != player:
                raise ContractError.LAND_NOT_OWNER
            is_renter = True
        else:
            if land["owner"] != player:
                raise ContractError.LAND_NOT_OWNER

        old_type = land["building_type"]
        old_level = land["building_level"]

        # Upgrade cost in gold tokens: level * 100 gold
        cost = U128(old_level + U64(1)) * U128(100)
        gold_token = self.storage.get("gold_token")
        contract_addr = self.env.current_contract_address()
        success = self.env.invoke_contract(gold_token, "transfer", [player, contract_addr, cost])
        if not success:
            raise ContractError.TRANSFER_FAILED

        # Update land
        land["building_type"] = building_type
        land["building_level"] = old_level + U64(1)
        self.storage.set(("land", x, y), land)

        # Recalculate neighbor bonuses for adjacent coordinates
        self._update_neighbors_bonus(x, y, old_type, old_level, building_type, land["building_level"])

        self.env.emit_event("building_placed", {
            "x": x,
            "y": y,
            "builder": player,
            "building_type": building_type,
            "level": land["building_level"]
        })

    @external
    def pay_taxes(self, payer: Address, x: I128, y: I128):
        """Pay property taxes for a land coordinate to prevent reclamation."""
        self._require_initialized()
        payer.require_auth()

        land = self.storage.get(("land", x, y), None)
        if land is None:
            raise ContractError.LAND_NOT_FOUND

        now = self.env.ledger().timestamp()
        
        # Taxes depend on building level (higher level = higher taxes)
        tax_multiplier = U128(land["building_level"] + U64(1))
        base_tax = self.storage.get("tax_price_per_week")
        tax_owed = base_tax * tax_multiplier

        gold_token = self.storage.get("gold_token")
        contract_addr = self.env.current_contract_address()
        success = self.env.invoke_contract(gold_token, "transfer", [payer, contract_addr, tax_owed])
        if not success:
            raise ContractError.TRANSFER_FAILED

        land["last_tax_payment"] = now
        self.storage.set(("land", x, y), land)

        self.env.emit_event("taxes_paid", {
            "x": x,
            "y": y,
            "payer": payer,
            "amount": tax_owed,
            "next_due": now + self.storage.get("tax_interval_sec")
        })

    @external
    def set_rental_terms(
        self,
        owner: Address,
        x: I128,
        y: I128,
        price_per_sec: U128
    ):
        """List a land parcel for rent. Only land owner."""
        self._require_initialized()
        owner.require_auth()

        land = self.storage.get(("land", x, y), None)
        if land is None:
            raise ContractError.LAND_NOT_FOUND
        if land["owner"] != owner:
            raise ContractError.LAND_NOT_OWNER

        now = self.env.ledger().timestamp()
        if land["renter"] != Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF") and land["rent_expires"] > now:
            raise ContractError.RENT_ACTIVE

        land["rent_price_per_sec"] = price_per_sec
        self.storage.set(("land", x, y), land)

        self.env.emit_event("rental_terms_updated", {
            "x": x,
            "y": y,
            "owner": owner,
            "price_per_sec": price_per_sec
        })

    @external
    def lease_land(self, renter: Address, x: I128, y: I128, duration_sec: U64):
        """Rent a listed land coordinate. Locks the payment in advance."""
        self._require_initialized()
        renter.require_auth()

        land = self.storage.get(("land", x, y), None)
        if land is None:
            raise ContractError.LAND_NOT_FOUND

        now = self.env.ledger().timestamp()
        # Verify no active rent lease is running
        if land["renter"] != Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF") and land["rent_expires"] > now:
            raise ContractError.RENT_ACTIVE

        price_per_sec = land["rent_price_per_sec"]
        if price_per_sec == U128(0):
            raise ContractError.INVALID_RENT_PRICE

        total_rent = price_per_sec * U128(duration_sec)

        # Pay rent directly to the land owner
        gold_token = self.storage.get("gold_token")
        success = self.env.invoke_contract(gold_token, "transfer", [renter, land["owner"], total_rent])
        if not success:
            raise ContractError.TRANSFER_FAILED

        land["renter"] = renter
        land["rent_expires"] = now + duration_sec
        self.storage.set(("land", x, y), land)

        self.env.emit_event("land_leased", {
            "x": x,
            "y": y,
            "renter": renter,
            "duration": duration_sec,
            "cost": total_rent
        })

    @external
    def liquidate_overdue_land(self, liquidator: Address, x: I128, y: I128):
        """Reclaim land if owner has neglected property taxes for more than 2 intervals (liquidation grace)."""
        self._require_initialized()
        liquidator.require_auth()

        land = self.storage.get(("land", x, y), None)
        if land is None:
            raise ContractError.LAND_NOT_FOUND

        now = self.env.ledger().timestamp()
        tax_interval = self.storage.get("tax_interval_sec")
        
        # Overdue threshold: 2 full tax intervals (e.g. 2 weeks)
        if now - land["last_tax_payment"] <= tax_interval * U64(2):
            raise ContractError.UNAUTHORIZED

        old_owner = land["owner"]

        # Liquidator pays a reclaim fee (50% base price) to purchase it
        base_price = self.storage.get("base_land_price")
        reclaim_fee = base_price / U128(2)

        gold_token = self.storage.get("gold_token")
        contract_addr = self.env.current_contract_address()
        success = self.env.invoke_contract(gold_token, "transfer", [liquidator, contract_addr, reclaim_fee])
        if not success:
            raise ContractError.TRANSFER_FAILED

        # Reassign owner and clear building/rent terms
        self._update_neighbors_bonus(x, y, land["building_type"], land["building_level"], Symbol("none"), U64(0))

        land["owner"] = liquidator
        land["building_type"] = Symbol("none")
        land["building_level"] = U64(0)
        land["last_tax_payment"] = now
        land["renter"] = Address.from_string("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF")
        land["rent_expires"] = U64(0)
        land["rent_price_per_sec"] = U128(0)

        self.storage.set(("land", x, y), land)
        self.storage.set(("land_owner", x, y), liquidator)

        self.env.emit_event("land_liquidated", {
            "x": x,
            "y": y,
            "old_owner": old_owner,
            "new_owner": liquidator
        })

    # ------------------------------------------------------------------ #
    #  View Operations                                                    #
    # ------------------------------------------------------------------ #

    @view
    def get_land(self, x: I128, y: I128) -> Map:
        """Get details of a land coordinate."""
        self._require_initialized()
        land = self.storage.get(("land", x, y), None)
        if land is None:
            raise ContractError.LAND_NOT_FOUND
        return land

    @view
    def get_neighbor_bonus(self, x: I128, y: I128) -> U64:
        """Get accumulated neighbor bonus score for coordinates."""
        self._require_initialized()
        return self.storage.get(("bonus", x, y), U64(0))

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

    def _abs_val(self, val: I128) -> I128:
        if val < I128(0):
            return -val
        return val

    def _update_neighbors_bonus(
        self,
        x: I128,
        y: I128,
        old_type: Symbol,
        old_level: U64,
        new_type: Symbol,
        new_level: U64
    ):
        """Update neighbor scores for direct adjacent fields (cross shape)."""
        # Calculate old and new bonus weights
        # E.g. farms/mines provide yield bonuses to neighbors based on level
        old_bonus = old_level * U64(10)
        new_bonus = new_level * U64(10)

        coords = [
            (x + I128(1), y),
            (x - I128(1), y),
            (x, y + I128(1)),
            (x, y - I128(1))
        ]

        for i in range(4):
            cx = coords[i][0]
            cy = coords[i][1]
            
            curr_bonus = self.storage.get(("bonus", cx, cy), U64(0))
            if curr_bonus >= old_bonus:
                adjusted = curr_bonus - old_bonus + new_bonus
            else:
                adjusted = new_bonus
            
            self.storage.set(("bonus", cx, cy), adjusted)
