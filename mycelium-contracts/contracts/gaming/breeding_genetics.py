"""
Breeding Genetics — Gene array crossover, dominant/recessive inheritance, mutations, and close-lineage penalty.

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
    PET_NOT_FOUND = 4
    NOT_OWNER = 5
    ON_COOLDOWN = 6
    SAME_PET = 7
    TRANSFER_FAILED = 8
    MUTATION_OVERFLOW = 9


# Limits
GENE_SLOTS = 8
BASE_MUTATION_BPS = U64(200) # 2%
INCEST_MUTATION_BPS = U64(1000) # 10%
BASE_BREEDING_COOLDOWN = U64(86400) # 24 hours


@contract
class BreedingGenetics:
    """Breeding Genetics contract managing pet generations, chromosome crossovers,
    recessive gene mutations, lineage tracking, and inbreeding penalties."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, gold_token: Address):
        """Initialize configurations."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("gold_token", gold_token)
        self.storage.set("pet_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin, "gold_token": gold_token})

    # ------------------------------------------------------------------ #
    #  Admin Operations                                                   #
    # ------------------------------------------------------------------ #

    @external
    def mint_generation_zero(
        self,
        admin: Address,
        recipient: Address,
        genes: Vec,
    ) -> U64:
        """Mint a Gen 0 pet with initial custom genes. Only Admin."""
        self._require_admin(admin)

        if len(genes) != GENE_SLOTS:
            raise ContractError.MUTATION_OVERFLOW

        pet_id = self.storage.get("pet_count", U64(0)) + U64(1)
        self.storage.set("pet_count", pet_id)

        pet = {
            "id": pet_id,
            "owner": recipient,
            "father_id": U64(0),
            "mother_id": U64(0),
            "generation": U64(0),
            "genes": genes, # list of U64, where each U64 packs Dominant (lower 32bit) and Recessive (upper 32bit)
            "breed_count": U64(0),
            "cooldown_end": U64(0),
            "incest_penalty": False,
        }

        self.storage.set(("pet", pet_id), pet)

        self.env.emit_event("gen_zero_minted", {
            "pet_id": pet_id,
            "owner": recipient,
            "genes": genes,
        })

        return pet_id

    # ------------------------------------------------------------------ #
    #  Breeding Actions                                                   #
    # ------------------------------------------------------------------ #

    @external
    def breed(self, player: Address, parent_a_id: U64, parent_b_id: U64) -> U64:
        """Breed two owned pets to create a new offspring.

        Args:
            player: Owner breeding the pets.
            parent_a_id: ID of parent A.
            parent_b_id: ID of parent B.
        """
        self._require_initialized()
        player.require_auth()

        if parent_a_id == parent_b_id:
            raise ContractError.SAME_PET

        p_a = self.storage.get(("pet", parent_a_id), None)
        p_b = self.storage.get(("pet", parent_b_id), None)

        if p_a is None or p_b is None:
            raise ContractError.PET_NOT_FOUND

        if p_a["owner"] != player or p_b["owner"] != player:
            raise ContractError.NOT_OWNER

        now = self.env.ledger().timestamp()

        # Check Cooldowns
        if now < p_a["cooldown_end"] or now < p_b["cooldown_end"]:
            raise ContractError.ON_COOLDOWN

        # Charge breeding fee in game Gold
        # Breed count increases fee: fee = 100 + (breed_count_A + breed_count_B) * 50
        breed_fee = U128(100) + U128(p_a["breed_count"] + p_b["breed_count"]) * U128(50)
        gold_token = self.storage.get("gold_token")
        contract_addr = self.env.current_contract_address()
        
        success = self.env.invoke_contract(gold_token, "transfer", [player, contract_addr, breed_fee])
        if not success:
            raise ContractError.TRANSFER_FAILED

        # Lineage Tracking & Close-lineage penalty check
        is_incest = self._check_incest(p_a, p_b)

        # Crossover & Mutation logic
        child_genes = Vec()
        entropy_seed = self.env.crypto().keccak256(now, parent_a_id, parent_b_id)

        mutation_rate = INCEST_MUTATION_BPS if is_incest else BASE_MUTATION_BPS

        for i in range(GENE_SLOTS):
            gene_a = p_a["genes"][i]
            gene_b = p_b["genes"][i]

            # Unpack Dominant & Recessive
            # Dominant: gene & 0xFFFFFFFF
            # Recessive: (gene >> 32) & 0xFFFFFFFF
            dom_a = gene_a % U64(4294967296)
            rec_a = gene_a / U64(4294967296)
            dom_b = gene_b % U64(4294967296)
            rec_b = gene_b / U64(4294967296)

            # Determine dominant & recessive crossover based on entropy
            roll = U64(int(entropy_seed[i % 32])) * U64(7) % U64(100) # 0..99
            
            # Crossover logic
            # Dominant choice
            if roll < U64(35):
                child_dom = dom_a
            elif roll < U64(70):
                child_dom = dom_b
            elif roll < U64(85):
                child_dom = rec_a
            else:
                child_dom = rec_b

            # Recessive choice (tie-break)
            roll_rec = U64(int(entropy_seed[(i + 1) % 32])) * U64(13) % U64(100)
            if roll_rec < U64(40):
                child_rec = rec_a
            elif roll_rec < U64(80):
                child_rec = rec_b
            else:
                child_rec = dom_a if child_dom != dom_a else dom_b

            # Mutation check
            mutation_roll = U64(int(entropy_seed[(i + 2) % 32])) * U64(29) % U64(10000)
            if mutation_roll < mutation_rate:
                # Mutated to a new trait value (0..999)
                child_dom = (mutation_roll + now) % U64(1000)
                child_rec = (mutation_roll * U64(3) + now) % U64(1000)

            # Pack genes back: (recessive << 32) | dominant
            packed = (child_rec * U64(4294967296)) + child_dom
            child_genes.append(packed)

        # Offspring generation is max(parent_gen) + 1
        child_gen = p_a["generation"] + U64(1)
        if p_b["generation"] > p_a["generation"]:
            child_gen = p_b["generation"] + U64(1)

        child_id = self.storage.get("pet_count", U64(0)) + U64(1)
        self.storage.set("pet_count", child_id)

        child_pet = {
            "id": child_id,
            "owner": player,
            "father_id": parent_a_id,
            "mother_id": parent_b_id,
            "generation": child_gen,
            "genes": child_genes,
            "breed_count": U64(0),
            "cooldown_end": U64(0),
            "incest_penalty": is_incest,
        }

        self.storage.set(("pet", child_id), child_pet)

        # Set Parents Cooldowns
        cooldown_dur_a = BASE_BREEDING_COOLDOWN * (p_a["breed_count"] + U64(1))
        cooldown_dur_b = BASE_BREEDING_COOLDOWN * (p_b["breed_count"] + U64(1))
        
        # Double breeding cooldown for incest parents
        if is_incest:
            cooldown_dur_a = cooldown_dur_a * U64(2)
            cooldown_dur_b = cooldown_dur_b * U64(2)

        p_a["cooldown_end"] = now + cooldown_dur_a
        p_a["breed_count"] = p_a["breed_count"] + U64(1)

        p_b["cooldown_end"] = now + cooldown_dur_b
        p_b["breed_count"] = p_b["breed_count"] + U64(1)

        self.storage.set(("pet", parent_a_id), p_a)
        self.storage.set(("pet", parent_b_id), p_b)

        self.env.emit_event("pet_bred", {
            "player": player,
            "child_id": child_id,
            "parent_a": parent_a_id,
            "parent_b": parent_b_id,
            "incest_penalty": is_incest,
        })

        return child_id

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

    def _check_incest(self, p_a: Map, p_b: Map) -> Bool:
        """Check if parents share sibling/parent relationships (incest)."""
        # Gen 0 cannot cause incest since parents are zero
        if p_a["id"] == U64(0) or p_b["id"] == U64(0):
            return False

        father_a = p_a["father_id"]
        mother_a = p_a["mother_id"]
        father_b = p_b["father_id"]
        mother_b = p_b["mother_id"]

        # 1. Sibling relationship (same father or same mother)
        if (father_a > U64(0) and father_a == father_b) or (mother_a > U64(0) and mother_a == mother_b):
            return True

        # 2. Parent-child relationship
        if father_a == p_b["id"] or mother_a == p_b["id"]:
            return True
        if father_b == p_a["id"] or mother_b == p_a["id"]:
            return True

        return False

    # ------------------------------------------------------------------ #
    #  View Functions                                                     #
    # ------------------------------------------------------------------ #

    @view
    def get_pet(self, pet_id: U64) -> Map:
        """Get details of a pet character."""
        self._require_initialized()
        pet = self.storage.get(("pet", pet_id), None)
        if pet is None:
            raise ContractError.PET_NOT_FOUND
        return pet

    @view
    def check_breeding_compatibility(self, pet_a_id: U64, pet_b_id: U64) -> Map:
        """Check compatibility between two pets before breeding."""
        self._require_initialized()
        p_a = self.storage.get(("pet", pet_a_id), None)
        p_b = self.storage.get(("pet", pet_b_id), None)
        if p_a is None or p_b is None:
            raise ContractError.PET_NOT_FOUND

        is_incest = self._check_incest(p_a, p_b)
        now = self.env.ledger().timestamp()

        ready_a = now >= p_a["cooldown_end"]
        ready_b = now >= p_b["cooldown_end"]

        return {
            "compatible": (pet_a_id != pet_b_id),
            "incest_risk": is_incest,
            "parent_a_ready": ready_a,
            "parent_b_ready": ready_b,
        }
