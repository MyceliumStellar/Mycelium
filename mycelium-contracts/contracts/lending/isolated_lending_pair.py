"""
Isolated Lending Pair — Isolated risk pair with elastic interest rates and closed liquidations.

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
    INSUFFICIENT_COLLATERAL = 4
    INSUFFICIENT_LIQUIDITY = 5
    LTV_EXCEEDED = 6
    NOT_LIQUIDATABLE = 7
    ZERO_AMOUNT = 8
    ZERO_PRICE = 9
    OVERFLOW = 10
    INVALID_PARAMETER = 11


# ── Constants ────────────────────────────────────────────────────────────────

WAD = U128(1_000_000_000_000_000_000)  # 1e18 scale
SECONDS_PER_YEAR = U128(31_536_000)
BPS_DENOMINATOR = U128(10000)

# Elastic Interest Rate Parameters
TARGET_UTILIZATION = U128(750_000_000_000_000_000)   # 75% target utilization
RATE_ADJUSTMENT_FACTOR = U128(500_000_000_000)       # Rate speed adjustment per second (scaled)
MIN_INTEREST_RATE = U128(5_000_000_000_000_000)      # 0.5% min annual rate
MAX_INTEREST_RATE = U128(1_000_000_000_000_000_000)  # 100% max annual rate


@contract
class IsolatedLendingPair:
    """
    Isolated risk lending pair.
    Only supports a single collateral and borrow asset pair (e.g. XLM/USDC).
    Uses an elastic interest rate model that increases/decreases the interest rate
    based on the deviation of actual utilization from the target utilization (75%).
    Liquidations are closed (direct buyout at a fixed discount).
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ── Initialization ───────────────────────────────────────────────────────

    @external
    def initialize(
        self,
        admin: Address,
        collateral_token: Address,
        borrow_token: Address,
        oracle: Address,
        collateral_decimals: U64,
        borrow_decimals: U64,
        ltv_bps: U128,              # e.g., 7500 for 75% LTV
        liq_threshold_bps: U128,    # e.g., 8000 for 80% Threshold
        liq_discount_bps: U128,     # e.g., 800 for 8% Discount
    ):
        """
        One-time contract initialization.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        if ltv_bps >= liq_threshold_bps or liq_threshold_bps >= BPS_DENOMINATOR:
            raise ContractError.INVALID_PARAMETER

        self.storage.set("admin", admin)
        self.storage.set("collateral_token", collateral_token)
        self.storage.set("borrow_token", borrow_token)
        self.storage.set("oracle", oracle)
        
        self.storage.set("collateral_decimals", collateral_decimals)
        self.storage.set("borrow_decimals", borrow_decimals)

        # Risk parameters
        self.storage.set("ltv", ltv_bps)
        self.storage.set("liq_threshold", liq_threshold_bps)
        self.storage.set("liq_discount", liq_discount_bps)

        # State initialization
        self.storage.set("total_collateral_amount", U128(0))
        self.storage.set("total_asset_shares", U128(0))
        self.storage.set("total_borrow_shares", U128(0))
        
        self.storage.set("borrow_index", WAD)
        self.storage.set("supply_index", WAD)
        self.storage.set("current_interest_rate", U128(50_000_000_000_000_000))  # 5% starting interest rate
        self.storage.set("last_update_time", self.env.ledger().timestamp())

        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "collateral": collateral_token,
            "borrow": borrow_token,
            "oracle": oracle,
        })

    # ── Liquidity & Collateral Management ────────────────────────────────────

    @external
    def add_collateral(self, caller: Address, amount: U128):
        """
        Deposits collateral into the pair. Does not earn interest.
        """
        caller.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        collateral_token = self.storage.get("collateral_token")
        self.env.transfer(caller, self.env.current_contract(), collateral_token, amount)

        # Scale amount to WAD (18 decimals)
        decimals = self.storage.get("collateral_decimals")
        amount_wad = amount * (10 ** (18 - decimals))

        user_coll = self.storage.get(f"collateral:{caller}", U128(0))
        self.storage.set(f"collateral:{caller}", user_coll + amount_wad)

        total_coll = self.storage.get("total_collateral_amount")
        self.storage.set("total_collateral_amount", total_coll + amount_wad)

        self.env.emit_event("collateral_added", {
            "user": caller,
            "amount": amount,
        })

    @external
    def remove_collateral(self, caller: Address, amount: U128):
        """
        Withdraws collateral. Reverts if it drops health factor below 1.0.
        """
        caller.require_auth()
        self._require_initialized()

        self._accrue_interest()

        user_coll = self.storage.get(f"collateral:{caller}", U128(0))
        decimals = self.storage.get("collateral_decimals")
        amount_wad = amount * (10 ** (18 - decimals))

        if amount_wad > user_coll:
            raise ContractError.INSUFFICIENT_COLLATERAL

        # Temp deduct
        self.storage.set(f"collateral:{caller}", user_coll - amount_wad)

        # Verify safety LTV
        self._require_safe_ltv(caller)

        total_coll = self.storage.get("total_collateral_amount")
        self.storage.set("total_collateral_amount", total_coll - amount_wad)

        # Transfer back
        collateral_token = self.storage.get("collateral_token")
        self.env.transfer(self.env.current_contract(), caller, collateral_token, amount)

        self.env.emit_event("collateral_removed", {
            "user": caller,
            "amount": amount,
        })

    # ── Asset Supply & Withdraw ──────────────────────────────────────────────

    @external
    def supply_asset(self, caller: Address, amount: U128):
        """
        Supplies borrow assets to earn interest.
        """
        caller.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        self._accrue_interest()
        
        borrow_token = self.storage.get("borrow_token")
        self.env.transfer(caller, self.env.current_contract(), borrow_token, amount)

        # Scale to WAD
        decimals = self.storage.get("borrow_decimals")
        amount_wad = amount * (10 ** (18 - decimals))

        supply_index = self.storage.get("supply_index")
        shares = (amount_wad * WAD) // supply_index

        user_shares = self.storage.get(f"asset_shares:{caller}", U128(0))
        self.storage.set(f"asset_shares:{caller}", user_shares + shares)

        total_shares = self.storage.get("total_asset_shares")
        self.storage.set("total_asset_shares", total_shares + shares)

        self.env.emit_event("asset_supplied", {
            "user": caller,
            "amount": amount,
            "shares": shares,
        })

    @external
    def withdraw_asset(self, caller: Address, amount: U128):
        """
        Withdraws supplied assets.
        """
        caller.require_auth()
        self._require_initialized()

        self._accrue_interest()

        supply_index = self.storage.get("supply_index")
        user_shares = self.storage.get(f"asset_shares:{caller}", U128(0))
        user_balance_wad = (user_shares * supply_index) // WAD

        decimals = self.storage.get("borrow_decimals")
        amount_wad = amount * (10 ** (18 - decimals))

        if amount_wad > user_balance_wad:
            raise ContractError.INSUFFICIENT_LIQUIDITY

        shares_to_burn = (amount_wad * WAD) // supply_index
        if shares_to_burn > user_shares:
            shares_to_burn = user_shares

        # Check pair cash liquidity
        borrow_token = self.storage.get("borrow_token")
        cash = self.env.token(borrow_token).balance(self.env.current_contract())
        if amount > cash:
            raise ContractError.INSUFFICIENT_LIQUIDITY

        self.storage.set(f"asset_shares:{caller}", user_shares - shares_to_burn)
        
        total_shares = self.storage.get("total_asset_shares")
        self.storage.set("total_asset_shares", total_shares - shares_to_burn)

        self.env.transfer(self.env.current_contract(), caller, borrow_token, amount)

        self.env.emit_event("asset_withdrawn", {
            "user": caller,
            "amount": amount,
            "shares": shares_to_burn,
        })

    # ── Borrow & Repay ───────────────────────────────────────────────────────

    @external
    def borrow(self, caller: Address, amount: U128):
        """
        Borrows assets against collateral. Reverts if it exceeds maximum LTV.
        """
        caller.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        self._accrue_interest()
        
        # Check cash liquidity
        borrow_token = self.storage.get("borrow_token")
        cash = self.env.token(borrow_token).balance(self.env.current_contract())
        if amount > cash:
            raise ContractError.INSUFFICIENT_LIQUIDITY

        decimals = self.storage.get("borrow_decimals")
        amount_wad = amount * (10 ** (18 - decimals))

        borrow_index = self.storage.get("borrow_index")
        shares = (amount_wad * WAD) // borrow_index

        user_shares = self.storage.get(f"borrow_shares:{caller}", U128(0))
        self.storage.set(f"borrow_shares:{caller}", user_shares + shares)

        total_shares = self.storage.get("total_borrow_shares")
        self.storage.set("total_borrow_shares", total_shares + shares)

        # Check safety
        self._require_safe_ltv(caller)

        self.env.transfer(self.env.current_contract(), caller, borrow_token, amount)

        self.env.emit_event("borrowed", {
            "user": caller,
            "amount": amount,
        })

    @external
    def repay(self, caller: Address, amount: U128, on_behalf_of: Address):
        """
        Repays borrow debt.
        """
        caller.require_auth()
        self._require_initialized()

        self._accrue_interest()

        borrow_shares = self.storage.get(f"borrow_shares:{on_behalf_of}", U128(0))
        borrow_index = self.storage.get("borrow_index")
        debt_wad = (borrow_shares * borrow_index) // WAD

        decimals = self.storage.get("borrow_decimals")
        amount_wad = amount * (10 ** (18 - decimals))

        repay_wad = amount_wad
        actual_repay = amount
        if repay_wad > debt_wad:
            repay_wad = debt_wad
            actual_repay = debt_wad // (10 ** (18 - decimals))

        shares_to_burn = (repay_wad * WAD) // borrow_index
        if shares_to_burn > borrow_shares:
            shares_to_burn = borrow_shares

        self.storage.set(f"borrow_shares:{on_behalf_of}", borrow_shares - shares_to_burn)
        
        total_shares = self.storage.get("total_borrow_shares")
        self.storage.set("total_borrow_shares", total_shares - shares_to_burn)

        borrow_token = self.storage.get("borrow_token")
        self.env.transfer(caller, self.env.current_contract(), borrow_token, actual_repay)

        self.env.emit_event("repaid", {
            "payer": caller,
            "borrower": on_behalf_of,
            "amount": actual_repay,
        })

    # ── Closed Liquidation ───────────────────────────────────────────────────

    @external
    def liquidate(self, caller: Address, user: Address, amount_to_repay: U128):
        """
        Liquidates unsafe borrower at a fixed discount.
        """
        caller.require_auth()
        self._require_initialized()

        self._accrue_interest()

        # Check if user is actually liquidatable
        health = self._get_health_factor(user)
        if health >= WAD:
            raise ContractError.NOT_LIQUIDATABLE

        borrow_shares = self.storage.get(f"borrow_shares:{user}", U128(0))
        borrow_index = self.storage.get("borrow_index")
        debt_wad = (borrow_shares * borrow_index) // WAD

        decimals = self.storage.get("borrow_decimals")
        repay_wad = amount_to_repay * (10 ** (18 - decimals))

        # Adjust repay amount to max user debt
        if repay_wad > debt_wad:
            repay_wad = debt_wad
            amount_to_repay = debt_wad // (10 ** (18 - decimals))

        # Get prices
        oracle = self.storage.get("oracle")
        collateral_token = self.storage.get("collateral_token")
        borrow_token = self.storage.get("borrow_token")
        
        coll_price = self.env.call(oracle, "get_price", [collateral_token])
        borrow_price = self.env.call(oracle, "get_price", [borrow_token])

        if coll_price == U128(0) or borrow_price == U128(0):
            raise ContractError.ZERO_PRICE

        # Value to seize in USD
        repay_usd = (repay_wad * borrow_price) // U128(100_000_000)
        
        # Apply closed discount (e.g. 8% discount => liquidator receives 108% of debt value in collateral)
        discount = self.storage.get("liq_discount")
        seize_usd = (repay_usd * (BPS_DENOMINATOR + discount)) // BPS_DENOMINATOR
        
        seize_collateral_wad = (seize_usd * U128(100_000_000)) // coll_price
        
        coll_decimals = self.storage.get("collateral_decimals")
        seize_collateral = seize_collateral_wad // (10 ** (18 - coll_decimals))

        # Check collateral bounds
        user_coll = self.storage.get(f"collateral:{user}", U128(0))
        if seize_collateral_wad > user_coll:
            # Scale down to what is available
            seize_collateral_wad = user_coll
            seize_collateral = user_coll // (10 ** (18 - coll_decimals))
            seize_usd = (seize_collateral_wad * coll_price) // U128(100_000_000)
            repay_usd = (seize_usd * BPS_DENOMINATOR) // (BPS_DENOMINATOR + discount)
            repay_wad = (repay_usd * U128(100_000_000)) // borrow_price
            amount_to_repay = repay_wad // (10 ** (18 - decimals))

        # Burn borrow shares
        shares_to_burn = (repay_wad * WAD) // borrow_index
        self.storage.set(f"borrow_shares:{user}", borrow_shares - shares_to_burn)
        
        total_borrow_sh = self.storage.get("total_borrow_shares")
        self.storage.set("total_borrow_shares", total_borrow_sh - shares_to_burn)

        # Deduct collateral
        self.storage.set(f"collateral:{user}", user_coll - seize_collateral_wad)
        
        total_coll = self.storage.get("total_collateral_amount")
        self.storage.set("total_collateral_amount", total_coll - seize_collateral_wad)

        # Transfer debt asset from liquidator
        self.env.transfer(caller, self.env.current_contract(), borrow_token, amount_to_repay)
        # Transfer collateral to liquidator
        self.env.transfer(self.env.current_contract(), caller, collateral_token, seize_collateral)

        self.env.emit_event("liquidated", {
            "user": user,
            "liquidator": caller,
            "repaid": amount_to_repay,
            "seized": seize_collateral,
        })

    # ── View Functions ───────────────────────────────────────────────────────

    @view
    def get_health(self, user: Address) -> U128:
        """Returns user health factor in WAD."""
        return self._get_health_factor(user)

    @view
    def get_pair_info(self) -> Map:
        """Returns config and state variables."""
        return {
            "collateral": self.storage.get("collateral_token"),
            "borrow": self.storage.get("borrow_token"),
            "interest_rate": self.storage.get("current_interest_rate"),
            "total_collateral": self.storage.get("total_collateral_amount"),
            "total_asset_shares": self.storage.get("total_asset_shares"),
            "total_borrow_shares": self.storage.get("total_borrow_shares"),
        }

    # ── Internal Helpers ─────────────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _accrue_interest(self):
        """
        Accrues interest and updates the interest rate elastically based on utilization.
        """
        now = self.env.ledger().timestamp()
        last_update = self.storage.get("last_update_time")
        time_elapsed = U128(now - last_update)
        if time_elapsed == U128(0):
            return

        borrow_shares = self.storage.get("total_borrow_shares")
        borrow_index = self.storage.get("borrow_index")
        total_borrow = (borrow_shares * borrow_index) // WAD

        # Get cash balance
        borrow_token = self.storage.get("borrow_token")
        cash = self.env.token(borrow_token).balance(self.env.current_contract())
        decimals = self.storage.get("borrow_decimals")
        cash_wad = cash * (10 ** (18 - decimals))

        total_liquidity = cash_wad + total_borrow
        utilization = U128(0)
        if total_liquidity > U128(0):
            utilization = (total_borrow * WAD) // total_liquidity

        # Accrue interest indices
        current_rate = self.storage.get("current_interest_rate")
        interest_factor = (current_rate * time_elapsed) // SECONDS_PER_YEAR
        
        new_borrow_index = (borrow_index * (WAD + interest_factor)) // WAD
        self.storage.set("borrow_index", new_borrow_index)

        # Accrue supply index (reserve factor is assumed zero for isolation simplicity)
        supply_index = self.storage.get("supply_index")
        supply_rate = (utilization * current_rate) // WAD
        supply_interest_factor = (supply_rate * time_elapsed) // SECONDS_PER_YEAR
        self.storage.set("supply_index", (supply_index * (WAD + supply_interest_factor)) // WAD)

        # ELASTIC INTEREST RATE MODEL
        # Adjust borrow rate dynamically based on target utilization deviation
        if utilization > TARGET_UTILIZATION:
            deviation = utilization - TARGET_UTILIZATION
            rate_delta = (deviation * RATE_ADJUSTMENT_FACTOR * time_elapsed) // WAD
            new_rate = current_rate + rate_delta
            if new_rate > MAX_INTEREST_RATE:
                new_rate = MAX_INTEREST_RATE
        else:
            deviation = TARGET_UTILIZATION - utilization
            rate_delta = (deviation * RATE_ADJUSTMENT_FACTOR * time_elapsed) // WAD
            new_rate = current_rate - rate_delta if current_rate > rate_delta else U128(0)
            if new_rate < MIN_INTEREST_RATE:
                new_rate = MIN_INTEREST_RATE

        self.storage.set("current_interest_rate", new_rate)
        self.storage.set("last_update_time", now)

    def _get_health_factor(self, user: Address) -> U128:
        """
        Health = (collateral_usd * threshold) / borrow_usd
        """
        user_coll = self.storage.get(f"collateral:{user}", U128(0))
        if user_coll == U128(0):
            return U128(0)

        borrow_shares = self.storage.get(f"borrow_shares:{user}", U128(0))
        if borrow_shares == U128(0):
            return WAD * U128(100)  # Infinite health

        oracle = self.storage.get("oracle")
        collateral_token = self.storage.get("collateral_token")
        borrow_token = self.storage.get("borrow_token")

        coll_price = self.env.call(oracle, "get_price", [collateral_token])
        borrow_price = self.env.call(oracle, "get_price", [borrow_token])

        if coll_price == U128(0) or borrow_price == U128(0):
            raise ContractError.ZERO_PRICE

        # WAD USD values
        coll_usd = (user_coll * coll_price) // U128(100_000_000)
        
        borrow_index = self.storage.get("borrow_index")
        debt_wad = (borrow_shares * borrow_index) // WAD
        borrow_usd = (debt_wad * borrow_price) // U128(100_000_000)

        liq_threshold = self.storage.get("liq_threshold")
        threshold_coll_usd = (coll_usd * liq_threshold) // BPS_DENOMINATOR

        return (threshold_coll_usd * WAD) // borrow_usd

    def _require_safe_ltv(self, user: Address):
        """
        Verifies user's current LTV limit is not exceeded.
        """
        borrow_shares = self.storage.get(f"borrow_shares:{user}", U128(0))
        if borrow_shares == U128(0):
            return

        user_coll = self.storage.get(f"collateral:{user}", U128(0))
        if user_coll == U128(0) and borrow_shares > U128(0):
            raise ContractError.LTV_EXCEEDED

        oracle = self.storage.get("oracle")
        collateral_token = self.storage.get("collateral_token")
        borrow_token = self.storage.get("borrow_token")

        coll_price = self.env.call(oracle, "get_price", [collateral_token])
        borrow_price = self.env.call(oracle, "get_price", [borrow_token])

        coll_usd = (user_coll * coll_price) // U128(100_000_000)

        borrow_index = self.storage.get("borrow_index")
        debt_wad = (borrow_shares * borrow_index) // WAD
        borrow_usd = (debt_wad * borrow_price) // U128(100_000_000)

        ltv = self.storage.get("ltv")
        max_borrow_usd = (coll_usd * ltv) // BPS_DENOMINATOR

        if borrow_usd > max_borrow_usd:
            raise ContractError.LTV_EXCEEDED
