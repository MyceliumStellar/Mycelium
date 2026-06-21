"""
Interest Rate Model — Jump-rate model with kink, utilization curves, and continuous compounding math.

Mycelium Smart Contract for Stellar
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


# ── Error Codes ──────────────────────────────────────────────────────────────

class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    ASSET_NOT_SUPPORTED = 4
    INVALID_PARAMETER = 5
    OVERFLOW = 6


# ── Constants ────────────────────────────────────────────────────────────────

WAD = U128(1_000_000_000_000_000_000)  # 1e18 scale
SECONDS_PER_YEAR = U128(31_536_000)
BPS_DENOMINATOR = U128(10000)


@contract
class InterestRateModel:
    """
    Interest Rate Model contract for Lending Pool.
    Implements utilization-based jump-rate calculations with an adjustable kink.
    Includes advanced continuous compounding factor calculations using Taylor Series
    expansion to approximate e^(r * dt) with high numerical precision.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """
        Initializes the contract. Sets the administrator.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("initialized", True)
        self.storage.set("supported_assets", Vec())

        self.env.emit_event("initialized", {"admin": admin})

    @external
    def set_asset_params(
        self,
        caller: Address,
        asset: Address,
        base_rate: U128,       # in WAD (e.g. 2% = 0.02 * 1e18)
        kink: U128,            # in WAD (e.g. 80% = 0.8 * 1e18)
        slope1: U128,          # in WAD, borrow rate multiplier below kink
        slope2: U128,          # in WAD, borrow rate multiplier above kink
        reserve_factor: U128,  # in bps (e.g. 1000 = 10%)
    ):
        """
        Sets or updates the interest rate curve parameters for an asset. Admin-only.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        if kink > WAD or reserve_factor > BPS_DENOMINATOR:
            raise ContractError.INVALID_PARAMETER

        params = {
            "base_rate": base_rate,
            "kink": kink,
            "slope1": slope1,
            "slope2": slope2,
            "reserve_factor": reserve_factor,
        }
        self.storage.set(f"params:{asset}", params)

        assets = self.storage.get("supported_assets")
        found = False
        for i in range(len(assets)):
            if assets[i] == asset:
                found = True
                break
        if not found:
            assets.append(asset)
            self.storage.set("supported_assets", assets)

        self.env.emit_event("asset_params_updated", {
            "asset": asset,
            "kink": kink,
            "base_rate": base_rate,
            "slope1": slope1,
            "slope2": slope2,
        })

    # ── View Functions ───────────────────────────────────────────────────────

    @view
    def get_utilization_rate(self, cash: U128, borrows: U128) -> U128:
        """
        Returns the utilization rate scaled to WAD: Borrows / (Cash + Borrows).
        """
        return self._calculate_utilization(cash, borrows)

    @view
    def get_borrow_rate(self, asset: Address, cash: U128, borrows: U128) -> U128:
        """
        Returns the annual borrow rate in WAD format.
        """
        params = self._get_params(asset)
        utilization = self._calculate_utilization(cash, borrows)
        return self._calculate_borrow_rate(params, utilization)

    @view
    def get_supply_rate(self, asset: Address, cash: U128, borrows: U128) -> U128:
        """
        Returns the annual supply rate in WAD format.
        Supply Rate = Utilization * Borrow Rate * (1 - Reserve Factor).
        """
        params = self._get_params(asset)
        utilization = self._calculate_utilization(cash, borrows)
        borrow_rate = self._calculate_borrow_rate(params, utilization)
        
        supply_rate = (utilization * borrow_rate) // WAD
        supply_rate = (supply_rate * (BPS_DENOMINATOR - params["reserve_factor"])) // BPS_DENOMINATOR
        return supply_rate

    @view
    def get_interest_indices_factors(
        self,
        asset: Address,
        cash: U128,
        borrows: U128,
        time_elapsed_seconds: U64,
    ) -> Vec:
        """
        Returns [borrow_factor, supply_factor] interest multipliers for the elapsed time.
        Uses Taylor series expansion for high-precision compounding: e^(r * dt).
        """
        params = self._get_params(asset)
        utilization = self._calculate_utilization(cash, borrows)

        # 1. Borrow compounding factor
        borrow_rate = self._calculate_borrow_rate(params, utilization)
        borrow_factor = self._calculate_compounding_factor(borrow_rate, time_elapsed_seconds)

        # 2. Supply compounding factor
        supply_rate = (utilization * borrow_rate) // WAD
        supply_rate = (supply_rate * (BPS_DENOMINATOR - params["reserve_factor"])) // BPS_DENOMINATOR
        supply_factor = self._calculate_compounding_factor(supply_rate, time_elapsed_seconds)

        return [borrow_factor, supply_factor]

    @view
    def get_asset_parameters(self, asset: Address) -> Map:
        """Returns the interest rate configuration of an asset."""
        return self._get_params(asset)

    # ── Internal Mathematical Helpers ────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _get_params(self, asset: Address) -> Map:
        params = self.storage.get(f"params:{asset}", None)
        if params is None:
            raise ContractError.ASSET_NOT_SUPPORTED
        return params

    def _calculate_utilization(self, cash: U128, borrows: U128) -> U128:
        total = cash + borrows
        if total == U128(0):
            return U128(0)
        return (borrows * WAD) // total

    def _calculate_borrow_rate(self, params: Map, utilization: U128) -> U128:
        base_rate = params["base_rate"]
        kink = params["kink"]
        slope1 = params["slope1"]
        slope2 = params["slope2"]

        if utilization <= kink:
            # borrow_rate = base_rate + utilization * slope1
            return base_rate + (utilization * slope1) // WAD
        else:
            # borrow_rate = base_rate + kink * slope1 + (utilization - kink) * slope2
            under_kink = (kink * slope1) // WAD
            above_kink = ((utilization - kink) * slope2) // WAD
            return base_rate + under_kink + above_kink

    def _calculate_compounding_factor(self, rate_annual: U128, dt_seconds: U64) -> U128:
        """
        Computes the compounding factor e^(rate * dt) via Taylor Series expansion:
        e^x = 1 + x + x^2 / 2! + x^3 / 6! + x^4 / 24! + ...
        Where x = rate_annual * dt_seconds / SECONDS_PER_YEAR.
        """
        if rate_annual == U128(0) or dt_seconds == U64(0):
            return WAD

        # x = rate_annual * dt / SECONDS_PER_YEAR
        dt_wad = U128(dt_seconds)
        x = (rate_annual * dt_wad) // SECONDS_PER_YEAR

        if x == U128(0):
            return WAD

        # Compute Taylor terms
        # Term 1: x
        term1 = x

        # Term 2: x^2 / 2
        term2 = (x * x) // (U128(2) * WAD)

        # Term 3: x^3 / 6
        term3 = (x * term2) // (U128(3) * WAD)

        # Term 4: x^4 / 24
        term4 = (x * term3) // (U128(4) * WAD)

        # Term 5: x^5 / 120
        term5 = (x * term4) // (U128(5) * WAD)

        # e^x ≈ WAD + term1 + term2 + term3 + term4 + term5
        factor = WAD + term1 + term2 + term3 + term4 + term5
        return factor
