"""
Lending Pool — Aave-style multi-asset lending and borrowing pool.

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
    ASSET_ALREADY_SUPPORTED = 5
    INSUFFICIENT_CASH = 6
    BORROW_HEALTH_FACTOR_TOO_LOW = 7
    WITHDRAW_HEALTH_FACTOR_TOO_LOW = 8
    ZERO_AMOUNT = 9
    LIQUIDATION_NOT_ALLOWED = 10
    REPAY_EXCEEDS_DEBT = 11
    ZERO_PRICE = 12
    ASSET_FROZEN = 13
    OVERFLOW = 14
    INVALID_PARAMETER = 15


# ── Constants ────────────────────────────────────────────────────────────────

WAD = U128(1_000_000_000_000_000_000)  # 1e18 scale for Wad arithmetic
SECONDS_PER_YEAR = U128(31_536_000)
CLOSE_FACTOR_BPS = U128(5000)          # 50% max liquidation close factor
BPS_DENOMINATOR = U128(10000)


@contract
class LendingPool:
    """
    Aave-style decentralized money market.
    Users can supply assets to earn interest and borrow other assets against 
    their collateral, subject to Loan-To-Value (LTV) limits and health factor checks.
    Interest rates adjust dynamically based on utilization.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ── Admin Operations ─────────────────────────────────────────────────────

    @external
    def initialize(self, admin: Address):
        """
        One-time contract initialization.
        Sets the administrator address.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("initialized", True)
        self.storage.set("assets_list", Vec())

        self.env.emit_event("initialized", {"admin": admin})

    @external
    def add_asset(
        self,
        caller: Address,
        asset: Address,
        decimals: U64,
        ltv: U128,
        liq_threshold: U128,
        liq_penalty: U128,
        reserve_factor: U128,
        base_rate: U128,
        kink: U128,
        slope1: U128,
        slope2: U128,
    ):
        """
        Admin-only: Registers a new asset supported by the lending pool.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        if ltv >= liq_threshold or liq_threshold >= BPS_DENOMINATOR:
            raise ContractError.INVALID_PARAMETER
        if kink > WAD or reserve_factor > BPS_DENOMINATOR:
            raise ContractError.INVALID_PARAMETER

        assets = self.storage.get("assets_list")
        for i in range(len(assets)):
            if assets[i] == asset:
                raise ContractError.ASSET_ALREADY_SUPPORTED

        # Store asset configuration
        config = {
            "decimals": decimals,
            "ltv": ltv,
            "liq_threshold": liq_threshold,
            "liq_penalty": liq_penalty,
            "reserve_factor": reserve_factor,
            "base_rate": base_rate,
            "kink": kink,
            "slope1": slope1,
            "slope2": slope2,
            "frozen": False,
        }
        self.storage.set(f"asset_config:{asset}", config)

        # Initialize asset indices and state variables
        state = {
            "supply_index": WAD,
            "borrow_index": WAD,
            "total_supply_shares": U128(0),
            "total_borrow_shares": U128(0),
            "total_reserve_fees": U128(0),
            "last_update_time": self.env.ledger().timestamp(),
        }
        self.storage.set(f"asset_state:{asset}", state)

        # Set default mock price to 1.0 USD (1e8)
        self.storage.set(f"asset_price:{asset}", U128(100_000_000))

        assets.append(asset)
        self.storage.set("assets_list", assets)

        self.env.emit_event("asset_added", {
            "asset": asset,
            "ltv": ltv,
            "liq_threshold": liq_threshold,
        })

    @external
    def set_price(self, caller: Address, asset: Address, price_usd: U128):
        """
        Set price of an asset in USD (with 8 decimals).
        Admin-only / Oracle feeder.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        if price_usd == U128(0):
            raise ContractError.ZERO_PRICE

        self._get_asset_config(asset)  # Verify asset is supported
        self.storage.set(f"asset_price:{asset}", price_usd)

        self.env.emit_event("price_updated", {
            "asset": asset,
            "price": price_usd,
        })

    @external
    def set_frozen(self, caller: Address, asset: Address, frozen: Bool):
        """
        Freeze or unfreeze an asset. Frozen assets do not allow supplies/borrows.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        config = self._get_asset_config(asset)
        config["frozen"] = frozen
        self.storage.set(f"asset_config:{asset}", config)

        self.env.emit_event("asset_frozen_status", {
            "asset": asset,
            "frozen": frozen,
        })

    # ── User Operations ──────────────────────────────────────────────────────

    @external
    def supply(self, caller: Address, asset: Address, amount: U128):
        """
        Supplies tokens to the pool. Earns interest dynamically.
        """
        caller.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        config = self._get_asset_config(asset)
        if config["frozen"]:
            raise ContractError.ASSET_FROZEN

        self._accrue_interest(asset)
        state = self._get_asset_state(asset)

        # Scale amount to WAD (1e18) for index calculations
        decimals = config["decimals"]
        amount_wad = amount * (10 ** (18 - decimals))

        # Calculate shares
        shares = (amount_wad * WAD) // state["supply_index"]

        # Update state
        state["total_supply_shares"] += shares
        self.storage.set(f"asset_state:{asset}", state)

        user_shares = self.storage.get(f"user_supply_shares:{caller}:{asset}", U128(0))
        self.storage.set(f"user_supply_shares:{caller}:{asset}", user_shares + shares)

        # Transfer tokens
        self.env.transfer(caller, self.env.current_contract(), asset, amount)

        self.env.emit_event("supplied", {
            "user": caller,
            "asset": asset,
            "amount": amount,
            "shares": shares,
        })

    @external
    def withdraw(self, caller: Address, asset: Address, amount: U128):
        """
        Withdraws supplied tokens. Reverts if it drops user's Health Factor below 1.0.
        """
        caller.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        config = self._get_asset_config(asset)
        self._accrue_interest(asset)
        state = self._get_asset_state(asset)

        # Calculate user balance in WAD
        user_shares = self.storage.get(f"user_supply_shares:{caller}:{asset}", U128(0))
        user_balance_wad = (user_shares * state["supply_index"]) // WAD

        decimals = config["decimals"]
        amount_wad = amount * (10 ** (18 - decimals))

        if amount_wad > user_balance_wad:
            raise ContractError.INSUFFICIENT_CASH

        # Calculate shares to burn
        shares_to_burn = (amount_wad * WAD) // state["supply_index"]
        if shares_to_burn > user_shares:
            shares_to_burn = user_shares

        # Temp deduct to evaluate health factor post-withdraw
        state["total_supply_shares"] -= shares_to_burn
        self.storage.set(f"asset_state:{asset}", state)
        self.storage.set(f"user_supply_shares:{caller}:{asset}", user_shares - shares_to_burn)

        # Check health factor
        health = self._get_health_factor(caller)
        if health < WAD:
            # Revert state change
            state["total_supply_shares"] += shares_to_burn
            self.storage.set(f"asset_state:{asset}", state)
            self.storage.set(f"user_supply_shares:{caller}:{asset}", user_shares)
            raise ContractError.WITHDRAW_HEALTH_FACTOR_TOO_LOW

        # Perform actual transfer
        self.env.transfer(self.env.current_contract(), caller, asset, amount)

        self.env.emit_event("withdrawn", {
            "user": caller,
            "asset": asset,
            "amount": amount,
            "shares": shares_to_burn,
        })

    @external
    def borrow(self, caller: Address, asset: Address, amount: U128):
        """
        Borrows tokens from pool. Reverts if Health Factor falls below 1.0.
        """
        caller.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        config = self._get_asset_config(asset)
        if config["frozen"]:
            raise ContractError.ASSET_FROZEN

        self._accrue_interest(asset)
        state = self._get_asset_state(asset)

        # Verify pool liquidity
        pool_cash = self.env.token(asset).balance(self.env.current_contract())
        if amount > pool_cash:
            raise ContractError.INSUFFICIENT_CASH

        # Scale amount to WAD
        decimals = config["decimals"]
        amount_wad = amount * (10 ** (18 - decimals))

        # Calculate borrow shares
        shares = (amount_wad * WAD) // state["borrow_index"]

        # Update state
        state["total_borrow_shares"] += shares
        self.storage.set(f"asset_state:{asset}", state)

        user_shares = self.storage.get(f"user_borrow_shares:{caller}:{asset}", U128(0))
        self.storage.set(f"user_borrow_shares:{caller}:{asset}", user_shares + shares)

        # Check health factor
        health = self._get_health_factor(caller)
        if health < WAD:
            # Revert state
            state["total_borrow_shares"] -= shares
            self.storage.set(f"asset_state:{asset}", state)
            self.storage.set(f"user_borrow_shares:{caller}:{asset}", user_shares)
            raise ContractError.BORROW_HEALTH_FACTOR_TOO_LOW

        # Transfer tokens to borrower
        self.env.transfer(self.env.current_contract(), caller, asset, amount)

        self.env.emit_event("borrowed", {
            "user": caller,
            "asset": asset,
            "amount": amount,
            "shares": shares,
        })

    @external
    def repay(self, caller: Address, asset: Address, amount: U128, on_behalf_of: Address):
        """
        Repays borrow position. Can be done by anyone on behalf of the borrower.
        """
        caller.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        config = self._get_asset_config(asset)
        self._accrue_interest(asset)
        state = self._get_asset_state(asset)

        # Calculate user borrow debt in WAD
        user_shares = self.storage.get(f"user_borrow_shares:{on_behalf_of}:{asset}", U128(0))
        user_debt_wad = (user_shares * state["borrow_index"]) // WAD

        decimals = config["decimals"]
        amount_wad = amount * (10 ** (18 - decimals))

        # Adjust repay amount if it exceeds debt
        repay_wad = amount_wad
        actual_repay_amount = amount
        if amount_wad > user_debt_wad:
            repay_wad = user_debt_wad
            actual_repay_amount = user_debt_wad // (10 ** (18 - decimals))

        # Calculate shares to burn
        shares_to_burn = (repay_wad * WAD) // state["borrow_index"]
        if shares_to_burn > user_shares:
            shares_to_burn = user_shares

        # Update state
        state["total_borrow_shares"] -= shares_to_burn
        self.storage.set(f"asset_state:{asset}", state)
        self.storage.set(f"user_borrow_shares:{on_behalf_of}:{asset}", user_shares - shares_to_burn)

        # Transfer tokens
        self.env.transfer(caller, self.env.current_contract(), asset, actual_repay_amount)

        self.env.emit_event("repaid", {
            "payer": caller,
            "borrower": on_behalf_of,
            "asset": asset,
            "amount": actual_repay_amount,
            "shares": shares_to_burn,
        })

    @external
    def liquidate(
        self,
        caller: Address,
        collateral_asset: Address,
        debt_asset: Address,
        user: Address,
        debt_to_cover: U128,
    ):
        """
        Liquidate an underwater position.
        The liquidator repays a portion of the user's debt and receives 
        an equivalent value of user's collateral plus a liquidation bonus.
        """
        caller.require_auth()
        self._require_initialized()

        if debt_to_cover == U128(0):
            raise ContractError.ZERO_AMOUNT

        # Accrue interest on both assets
        self._accrue_interest(collateral_asset)
        self._accrue_interest(debt_asset)

        # Check if user is actually liquidatable
        health = self._get_health_factor(user)
        if health >= WAD:
            raise ContractError.LIQUIDATION_NOT_ALLOWED

        # Get states
        debt_state = self._get_asset_state(debt_asset)
        coll_state = self._get_asset_state(collateral_asset)
        debt_config = self._get_asset_config(debt_asset)
        coll_config = self._get_asset_config(collateral_asset)

        # Check maximum allowable repay (close factor = 50%)
        user_debt_shares = self.storage.get(f"user_borrow_shares:{user}:{debt_asset}", U128(0))
        user_debt_wad = (user_debt_shares * debt_state["borrow_index"]) // WAD

        debt_to_cover_wad = debt_to_cover * (10 ** (18 - debt_config["decimals"]))
        max_repay_wad = (user_debt_wad * CLOSE_FACTOR_BPS) // BPS_DENOMINATOR
        
        if debt_to_cover_wad > max_repay_wad:
            debt_to_cover_wad = max_repay_wad
            debt_to_cover = max_repay_wad // (10 ** (18 - debt_config["decimals"]))

        # Calculate values in USD
        debt_price = self.storage.get(f"asset_price:{debt_asset}", U128(0))
        coll_price = self.storage.get(f"asset_price:{collateral_asset}", U128(0))
        if debt_price == U128(0) or coll_price == U128(0):
            raise ContractError.ZERO_PRICE

        # Value of debt being repaid in USD (18 decimals)
        debt_value_usd = (debt_to_cover_wad * debt_price) // U128(100_000_000)

        # Collateral reward to liquidator (including liquidation penalty bonus)
        # penalty bps = e.g., 500 bps (5% bonus). Total reward is 105% of debt value
        penalty_bps = coll_config["liq_penalty"]
        collateral_reward_usd = (debt_value_usd * (BPS_DENOMINATOR + penalty_bps)) // BPS_DENOMINATOR

        # Convert collateral reward to collateral WAD
        collateral_reward_wad = (collateral_reward_usd * U128(100_000_000)) // coll_price

        # Check if user has enough collateral
        user_coll_shares = self.storage.get(f"user_supply_shares:{user}:{collateral_asset}", U128(0))
        user_coll_wad = (user_coll_shares * coll_state["supply_index"]) // WAD

        if collateral_reward_wad > user_coll_wad:
            # Scale down liquidation sizes to fit available collateral
            collateral_reward_wad = user_coll_wad
            collateral_reward_usd = (collateral_reward_wad * coll_price) // U128(100_000_000)
            debt_value_usd = (collateral_reward_usd * BPS_DENOMINATOR) // (BPS_DENOMINATOR + penalty_bps)
            debt_to_cover_wad = (debt_value_usd * U128(100_000_000)) // debt_price
            debt_to_cover = debt_to_cover_wad // (10 ** (18 - debt_config["decimals"]))

        # Execution of liquidation updates
        debt_shares_to_burn = (debt_to_cover_wad * WAD) // debt_state["borrow_index"]
        coll_shares_to_burn = (collateral_reward_wad * WAD) // coll_state["supply_index"]

        # Deduct debt from borrower
        debt_state["total_borrow_shares"] -= debt_shares_to_burn
        self.storage.set(f"asset_state:{debt_asset}", debt_state)
        self.storage.set(f"user_borrow_shares:{user}:{debt_asset}", user_debt_shares - debt_shares_to_burn)

        # Deduct collateral from borrower
        coll_state["total_supply_shares"] -= coll_shares_to_burn
        self.storage.set(f"asset_state:{collateral_asset}", coll_state)
        self.storage.set(f"user_supply_shares:{user}:{collateral_asset}", user_coll_shares - coll_shares_to_burn)

        # Perform tokens transfers
        # 1. Liquidator transfers debt asset to contract
        self.env.transfer(caller, self.env.current_contract(), debt_asset, debt_to_cover)
        # 2. Contract transfers collateral asset to liquidator
        collateral_to_transfer = collateral_reward_wad // (10 ** (18 - coll_config["decimals"]))
        self.env.transfer(self.env.current_contract(), caller, collateral_asset, collateral_to_transfer)

        self.env.emit_event("liquidated", {
            "liquidator": caller,
            "borrower": user,
            "debt_asset": debt_asset,
            "collateral_asset": collateral_asset,
            "debt_repaid": debt_to_cover,
            "collateral_seized": collateral_to_transfer,
        })

    # ── View Functions ───────────────────────────────────────────────────────

    @view
    def get_health_factor(self, user: Address) -> U128:
        """
        Returns health factor scaled to 1e18. Values above 1e18 are healthy.
        """
        return self._get_health_factor(user)

    @view
    def get_asset_info(self, asset: Address) -> Map:
        """
        Returns configuration, price and state variables of an asset.
        """
        config = self._get_asset_config(asset)
        state = self._get_asset_state(asset)
        price = self.storage.get(f"asset_price:{asset}", U128(0))
        return {
            "config": config,
            "state": state,
            "price": price,
        }

    @view
    def get_user_balance(self, user: Address, asset: Address) -> Vec:
        """
        Returns [supplied_amount, borrowed_amount] of a user for a specific asset.
        """
        config = self._get_asset_config(asset)
        state = self._get_asset_state(asset)
        
        supply_shares = self.storage.get(f"user_supply_shares:{user}:{asset}", U128(0))
        borrow_shares = self.storage.get(f"user_borrow_shares:{user}:{asset}", U128(0))

        supply_amount = (supply_shares * state["supply_index"]) // WAD
        borrow_amount = (borrow_shares * state["borrow_index"]) // WAD

        # Convert back from WAD to original decimals
        decimals = config["decimals"]
        raw_supply = supply_amount // (10 ** (18 - decimals))
        raw_borrow = borrow_amount // (10 ** (18 - decimals))

        return [raw_supply, raw_borrow]

    @view
    def get_assets(self) -> Vec:
        """Returns the list of supported assets."""
        return self.storage.get("assets_list", Vec())

    # ── Internal Helpers ─────────────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _get_asset_config(self, asset: Address) -> Map:
        config = self.storage.get(f"asset_config:{asset}", None)
        if config is None:
            raise ContractError.ASSET_NOT_SUPPORTED
        return config

    def _get_asset_state(self, asset: Address) -> Map:
        state = self.storage.get(f"asset_state:{asset}", None)
        if state is None:
            raise ContractError.ASSET_NOT_SUPPORTED
        return state

    def _accrue_interest(self, asset: Address):
        """
        Calculates and updates accumulated supply and borrow indices 
        based on time elapsed since last update.
        """
        state = self._get_asset_state(asset)
        config = self._get_asset_config(asset)
        now = self.env.ledger().timestamp()
        
        time_elapsed = U128(now - state["last_update_time"])
        if time_elapsed == U128(0):
            return

        # Calculate utilization
        total_borrow = (state["total_borrow_shares"] * state["borrow_index"]) // WAD
        cash = self.env.token(asset).balance(self.env.current_contract())
        
        # Convert cash to WAD
        cash_wad = cash * (10 ** (18 - config["decimals"]))
        total_liquidity = cash_wad + total_borrow

        utilization = U128(0)
        if total_liquidity > U128(0):
            utilization = (total_borrow * WAD) // total_liquidity

        # Calculate borrow rate using jump rate model
        base_rate = config["base_rate"]
        kink = config["kink"]
        slope1 = config["slope1"]
        slope2 = config["slope2"]

        if utilization <= kink:
            borrow_rate = base_rate + (utilization * slope1) // WAD
        else:
            borrow_rate = base_rate + (kink * slope1) // WAD + ((utilization - kink) * slope2) // WAD

        # Calculate borrow index update
        interest_factor = (borrow_rate * time_elapsed) // SECONDS_PER_YEAR
        state["borrow_index"] = (state["borrow_index"] * (WAD + interest_factor)) // WAD

        # Calculate supply rate and supply index update
        # supply_rate = utilization * borrow_rate * (1 - reserve_factor)
        reserve_factor = config["reserve_factor"]
        supply_rate = (utilization * borrow_rate) // WAD
        supply_rate = (supply_rate * (BPS_DENOMINATOR - reserve_factor)) // BPS_DENOMINATOR

        supply_interest_factor = (supply_rate * time_elapsed) // SECONDS_PER_YEAR
        state["supply_index"] = (state["supply_index"] * (WAD + supply_interest_factor)) // WAD

        # Reserve fees accrue to the pool treasury
        accrued_interest = (total_borrow * interest_factor) // WAD
        reserve_fees = (accrued_interest * reserve_factor) // BPS_DENOMINATOR
        state["total_reserve_fees"] += reserve_fees

        state["last_update_time"] = now
        self.storage.set(f"asset_state:{asset}", state)

    def _get_health_factor(self, user: Address) -> U128:
        """
        Evaluates user's collateral vs debt.
        Health = Sum(collateral_i_usd * threshold_i) / Sum(debt_i_usd)
        """
        assets = self.storage.get("assets_list", Vec())
        total_collateral_threshold_usd = U128(0)
        total_debt_usd = U128(0)

        for i in range(len(assets)):
            asset = assets[i]
            config = self._get_asset_config(asset)
            state = self._get_asset_state(asset)
            price = self.storage.get(f"asset_price:{asset}", U128(0))

            if price == U128(0):
                continue

            # Supply balance
            supply_shares = self.storage.get(f"user_supply_shares:{user}:{asset}", U128(0))
            if supply_shares > U128(0):
                # We do not accrue interest dynamically during view to avoid storage modifications,
                # but health calculations should use current indices which are assumed fresh or 
                # approximated here.
                supply_amount_wad = (supply_shares * state["supply_index"]) // WAD
                supply_usd = (supply_amount_wad * price) // U128(100_000_000)
                total_collateral_threshold_usd += (supply_usd * config["liq_threshold"]) // BPS_DENOMINATOR

            # Borrow balance
            borrow_shares = self.storage.get(f"user_borrow_shares:{user}:{asset}", U128(0))
            if borrow_shares > U128(0):
                borrow_amount_wad = (borrow_shares * state["borrow_index"]) // WAD
                borrow_usd = (borrow_amount_wad * price) // U128(100_000_000)
                total_debt_usd += borrow_usd

        if total_debt_usd == U128(0):
            return WAD * U128(100)  # Infinite health factor if no debt

        return (total_collateral_threshold_usd * WAD) // total_debt_usd
