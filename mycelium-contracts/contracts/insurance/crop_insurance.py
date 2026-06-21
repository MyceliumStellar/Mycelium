"""
Crop Insurance — Regional weather-triggered agricultural policy contract.

Mycelium Smart Contract for Stellar
Enables farmers to register land coordinate bounds, buy seasonal policies with
subsidy assistance, and receive automatic payouts when regional weather oracles
report droughts, freezes, or excessive precipitation.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    INVALID_PARAMETERS = 4
    FARM_NOT_REGISTERED = 5
    FARM_ALREADY_EXISTS = 6
    CROP_RISK_NOT_FOUND = 7
    POLICY_NOT_FOUND = 8
    POLICY_ALREADY_EVALUATED = 9
    SEASON_NOT_ACTIVE = 10
    SEASON_NOT_ENDED = 11
    WEATHER_DATA_MISSING = 12
    INSUFFICIENT_POOL_BALANCE = 13


@contract
class CropInsurance:
    """
    Parametric crop insurance contract using weather oracle reports, region-specific
    subsidy pools, and automated payout evaluations based on seasonal metrics.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        asset_token: Address,
        oracle: Address,
    ):
        """Initialize the crop insurance contract."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("asset_token", asset_token)
        self.storage.set("oracle", oracle)
        self.storage.set("policy_count", U64(0))
        self.storage.set("pool_balance", U128(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "asset_token": asset_token,
            "oracle": oracle,
        })

    @external
    def set_oracle(self, admin: Address, oracle: Address):
        """Change the whitelisted weather oracle."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        self.storage.set("oracle", oracle)
        self.env.emit_event("oracle_updated", {"oracle": oracle})

    @external
    def set_crop_risk(self, admin: Address, crop_type: Symbol, risk_bps: U64):
        """Set or update risk multiplier in basis points for a crop type."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        if risk_bps == 0 or risk_bps > 10000:
            raise ContractError.INVALID_PARAMETERS

        self.storage.set(f"crop_risk:{crop_type}", risk_bps)
        self.env.emit_event("crop_risk_updated", {
            "crop_type": crop_type,
            "risk_bps": risk_bps,
        })

    @external
    def register_farm(
        self,
        farmer: Address,
        farm_id: U64,
        region_id: Symbol,
        crop_type: Symbol,
        acres: U64,
    ):
        """Register farm location coordinates, crop specifications, and size."""
        farmer.require_auth()
        self._require_initialized()

        if acres == 0:
            raise ContractError.INVALID_PARAMETERS

        if self.storage.get(f"farm:{farmer}:{farm_id}:registered", False):
            raise ContractError.FARM_ALREADY_EXISTS

        self.storage.set(f"farm:{farmer}:{farm_id}:registered", True)
        self.storage.set(f"farm:{farmer}:{farm_id}:region_id", region_id)
        self.storage.set(f"farm:{farmer}:{farm_id}:crop_type", crop_type)
        self.storage.set(f"farm:{farmer}:{farm_id}:acres", acres)

        self.env.emit_event("farm_registered", {
            "farmer": farmer,
            "farm_id": farm_id,
            "region_id": region_id,
            "crop_type": crop_type,
            "acres": acres,
        })

    @external
    def deposit_subsidy(
        self,
        provider: Address,
        region_id: Symbol,
        crop_type: Symbol,
        amount: U128,
    ):
        """Deposit funds into a regional crop subsidy pool."""
        provider.require_auth()
        self._require_initialized()

        if amount == 0:
            raise ContractError.INVALID_PARAMETERS

        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, provider, self.env.current_contract(), amount)

        current_subsidy = self.storage.get(f"subsidy:{region_id}:{crop_type}", U128(0))
        self.storage.set(f"subsidy:{region_id}:{crop_type}", current_subsidy + amount)

        self.env.emit_event("subsidy_deposited", {
            "provider": provider,
            "region_id": region_id,
            "crop_type": crop_type,
            "amount": amount,
        })

    @external
    def buy_policy(
        self,
        farmer: Address,
        farm_id: U64,
        coverage_amount: U128,
        season_id: U64,
    ) -> U64:
        """Purchase a seasonal weather-indexed crop insurance policy."""
        farmer.require_auth()
        self._require_initialized()

        if not self.storage.get(f"farm:{farmer}:{farm_id}:registered", False):
            raise ContractError.FARM_NOT_REGISTERED

        region_id = self.storage.get(f"farm:{farmer}:{farm_id}:region_id")
        crop_type = self.storage.get(f"farm:{farmer}:{farm_id}:crop_type")
        acres = self.storage.get(f"farm:{farmer}:{farm_id}:acres")

        risk_bps = self.storage.get(f"crop_risk:{crop_type}", None)
        if risk_bps is None:
            raise ContractError.CROP_RISK_NOT_FOUND

        if coverage_amount == 0:
            raise ContractError.INVALID_PARAMETERS

        # Premium Calculation: Base Premium = Coverage * risk_bps * acres / 10,000 / 10 (scaling factor)
        total_premium = (coverage_amount * U128(risk_bps) * U128(acres)) // U128(100000)
        if total_premium == 0:
            total_premium = U128(1)

        # Subsidy logic: Government covers up to 50% of the premium if pool has funds
        subsidy_eligible = total_premium // U128(2)
        subsidy_pool = self.storage.get(f"subsidy:{region_id}:{crop_type}", U128(0))

        subsidy_applied = U128(0)
        if subsidy_pool >= subsidy_eligible:
            subsidy_applied = subsidy_eligible
            self.storage.set(f"subsidy:{region_id}:{crop_type}", subsidy_pool - subsidy_applied)
        elif subsidy_pool > 0:
            subsidy_applied = subsidy_pool
            self.storage.set(f"subsidy:{region_id}:{crop_type}", U128(0))

        farmer_premium = total_premium - subsidy_applied

        asset_token = self.storage.get("asset_token")
        # Collect remaining premium from farmer
        if farmer_premium > 0:
            self.env.transfer(asset_token, farmer, self.env.current_contract(), farmer_premium)

        # Update contract pool balance
        pool_balance = self.storage.get("pool_balance", U128(0))
        self.storage.set("pool_balance", pool_balance + farmer_premium + subsidy_applied)

        policy_id = self.storage.get("policy_count") + 1
        self.storage.set("policy_count", policy_id)

        self.storage.set(f"policy:{policy_id}:farmer", farmer)
        self.storage.set(f"policy:{policy_id}:farm_id", farm_id)
        self.storage.set(f"policy:{policy_id}:region_id", region_id)
        self.storage.set(f"policy:{policy_id}:coverage", coverage_amount)
        self.storage.set(f"policy:{policy_id}:premium", total_premium)
        self.storage.set(f"policy:{policy_id}:subsidy_applied", subsidy_applied)
        self.storage.set(f"policy:{policy_id}:season_id", season_id)
        self.storage.set(f"policy:{policy_id}:evaluated", False)

        self.env.emit_event("policy_purchased", {
            "policy_id": policy_id,
            "farmer": farmer,
            "coverage": coverage_amount,
            "premium": total_premium,
            "subsidy_applied": subsidy_applied,
            "season_id": season_id,
        })

        return policy_id

    @external
    def report_weather(
        self,
        oracle: Address,
        region_id: Symbol,
        season_id: U64,
        precipitation_mm: U64,
        temperature_c: I128,
        drought_index: U64,
    ):
        """Oracle reports crop season weather data for a region."""
        oracle.require_auth()
        self._require_initialized()
        self._require_oracle(oracle)

        # Store parameters
        prefix = f"weather:{region_id}:{season_id}"
        self.storage.set(f"{prefix}:precipitation", precipitation_mm)
        self.storage.set(f"{prefix}:temperature", temperature_c)
        self.storage.set(f"{prefix}:drought", drought_index)
        self.storage.set(f"{prefix}:reported", True)

        self.env.emit_event("weather_reported", {
            "region_id": region_id,
            "season_id": season_id,
            "precipitation": precipitation_mm,
            "temperature": temperature_c,
            "drought_index": drought_index,
        })

    @external
    def evaluate_policy(self, caller: Address, policy_id: U64):
        """Evaluate a crop policy against seasonal weather triggers and payout if conditions are met."""
        caller.require_auth()
        self._require_initialized()

        farmer = self.storage.get(f"policy:{policy_id}:farmer", None)
        if farmer is None:
            raise ContractError.POLICY_NOT_FOUND

        if self.storage.get(f"policy:{policy_id}:evaluated", False):
            raise ContractError.POLICY_ALREADY_EVALUATED

        region_id = self.storage.get(f"policy:{policy_id}:region_id")
        season_id = self.storage.get(f"policy:{policy_id}:season_id")

        prefix = f"weather:{region_id}:{season_id}"
        if not self.storage.get(f"{prefix}:reported", False):
            raise ContractError.WEATHER_DATA_MISSING

        precipitation = self.storage.get(f"{prefix}:precipitation")
        temperature = self.storage.get(f"{prefix}:temperature")
        drought = self.storage.get(f"{prefix}:drought")

        coverage = self.storage.get(f"policy:{policy_id}:coverage")
        payout_bps = U64(0)

        # Trigger logic:
        # 1. Drought index: if >= 80, 100% payout; if >= 60, 50% payout; if >= 40, 20% payout
        # 2. Extreme Cold (Freeze): if temperature < 0C, 40% payout
        # 3. Drought precipitation: precipitation < 100mm, 30% payout; < 50mm, 60% payout
        if drought >= 80:
            payout_bps = U64(10000)
        elif drought >= 60:
            payout_bps = U64(5000)
        elif drought >= 40:
            payout_bps = U64(2000)

        # Freeze check
        if temperature < 0:
            if payout_bps < 4000:
                payout_bps = U64(4000)

        # Drought rainfall check
        if precipitation < 50:
            if payout_bps < 6000:
                payout_bps = U64(6000)
        elif precipitation < 100:
            if payout_bps < 3000:
                payout_bps = U64(3000)

        payout_amount = (coverage * U128(payout_bps)) // U128(10000)
        pool_balance = self.storage.get("pool_balance", U128(0))

        if payout_amount > 0:
            if payout_amount > pool_balance:
                payout_amount = pool_balance

            self.storage.set("pool_balance", pool_balance - payout_amount)
            asset_token = self.storage.get("asset_token")
            self.env.transfer(asset_token, self.env.current_contract(), farmer, payout_amount)

        self.storage.set(f"policy:{policy_id}:evaluated", True)
        self.storage.set(f"policy:{policy_id}:payout", payout_amount)

        self.env.emit_event("policy_evaluated", {
            "policy_id": policy_id,
            "farmer": farmer,
            "payout_amount": payout_amount,
            "satisfied_bps": payout_bps,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_policy(self, policy_id: U64) -> Map:
        """Get details of a crop policy."""
        farmer = self.storage.get(f"policy:{policy_id}:farmer", None)
        if farmer is None:
            raise ContractError.POLICY_NOT_FOUND

        return {
            "policy_id": policy_id,
            "farmer": farmer,
            "farm_id": self.storage.get(f"policy:{policy_id}:farm_id"),
            "region_id": self.storage.get(f"policy:{policy_id}:region_id"),
            "coverage": self.storage.get(f"policy:{policy_id}:coverage"),
            "premium": self.storage.get(f"policy:{policy_id}:premium"),
            "subsidy_applied": self.storage.get(f"policy:{policy_id}:subsidy_applied"),
            "season_id": self.storage.get(f"policy:{policy_id}:season_id"),
            "evaluated": self.storage.get(f"policy:{policy_id}:evaluated"),
            "payout": self.storage.get(f"policy:{policy_id}:payout", U128(0)),
        }

    @view
    def get_farm(self, farmer: Address, farm_id: U64) -> Map:
        """Get farmer's registered farm details."""
        if not self.storage.get(f"farm:{farmer}:{farm_id}:registered", False):
            raise ContractError.FARM_NOT_REGISTERED

        return {
            "farmer": farmer,
            "farm_id": farm_id,
            "region_id": self.storage.get(f"farm:{farmer}:{farm_id}:region_id"),
            "crop_type": self.storage.get(f"farm:{farmer}:{farm_id}:crop_type"),
            "acres": self.storage.get(f"farm:{farmer}:{farm_id}:acres"),
        }

    @view
    def get_subsidy_balance(self, region_id: Symbol, crop_type: Symbol) -> U128:
        """Get the current subsidy balance for a region and crop type."""
        return self.storage.get(f"subsidy:{region_id}:{crop_type}", U128(0))

    @view
    def get_weather_data(self, region_id: Symbol, season_id: U64) -> Map:
        """Get reported weather metrics for a region's season."""
        prefix = f"weather:{region_id}:{season_id}"
        if not self.storage.get(f"{prefix}:reported", False):
            raise ContractError.WEATHER_DATA_MISSING

        return {
            "precipitation": self.storage.get(f"{prefix}:precipitation"),
            "temperature": self.storage.get(f"{prefix}:temperature"),
            "drought": self.storage.get(f"{prefix}:drought"),
        }

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_oracle(self, caller: Address):
        oracle = self.storage.get("oracle")
        if caller != oracle:
            raise ContractError.UNAUTHORIZED
