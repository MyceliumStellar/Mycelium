"""
Liquidation Engine — Dutch auction liquidator with keeper bonuses and bad debt socialization.

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
    POSITION_NOT_UNDERWATER = 4
    AUCTION_NOT_FOUND = 5
    AUCTION_ALREADY_ACTIVE = 6
    CLOSE_FACTOR_EXCEEDED = 7
    INSUFFICIENT_COLLATERAL = 8
    STABILITY_POOL_EMPTY = 9
    ZERO_AMOUNT = 10
    OVERFLOW = 11
    INVALID_PARAMETER = 12
    AUCTION_NOT_EXPIRED = 13


# ── Constants ────────────────────────────────────────────────────────────────

WAD = U128(1_000_000_000_000_000_000)  # 1e18 scale
BPS_DENOMINATOR = U128(10000)
CLOSE_FACTOR_BPS = U128(5000)          # 50% max close factor
AUCTION_DURATION = U64(3600)           # 1 hour auction duration
START_BONUS_BPS = U128(200)            # 2% starting liquidation bonus
MAX_BONUS_BPS = U128(1500)             # 15% maximum Dutch auction bonus


@contract
class LiquidationEngine:
    """
    Dedicated Dutch Auction Liquidation Engine.
    Monitors position health. If a position falls below the liquidation threshold,
    keepers can trigger a Dutch auction.
    The liquidator's bonus starts at 2% and increases linearly to 15% over 1 hour.
    If the auction expires without being filled and debt exceeds collateral,
    the bad debt is socialized using a pre-funded stability pool.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ── Administrative Functions ─────────────────────────────────────────────

    @external
    def initialize(self, admin: Address, lending_pool: Address):
        """
        Initializes the liquidation core engine.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("lending_pool", lending_pool)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "lending_pool": lending_pool,
        })

    # ── Stability Pool Management ────────────────────────────────────────────

    @external
    def fund_stability_pool(self, caller: Address, asset: Address, amount: U128):
        """
        Lenders deposit funds into the stability pool to socialize bad debts.
        """
        caller.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        self.env.transfer(caller, self.env.current_contract(), asset, amount)

        pool_balance = self.storage.get(f"stability_pool:{asset}", U128(0))
        self.storage.set(f"stability_pool:{asset}", pool_balance + amount)

        self.env.emit_event("stability_pool_funded", {
            "funder": caller,
            "asset": asset,
            "amount": amount,
        })

    @external
    def withdraw_stability_pool(self, caller: Address, asset: Address, amount: U128):
        """
        Lenders withdraw funds from the stability pool.
        """
        caller.require_auth()
        self._require_initialized()

        pool_balance = self.storage.get(f"stability_pool:{asset}", U128(0))
        if amount > pool_balance:
            raise ContractError.INVALID_PARAMETER

        self.storage.set(f"stability_pool:{asset}", pool_balance - amount)
        self.env.transfer(self.env.current_contract(), caller, asset, amount)

        self.env.emit_event("stability_pool_withdrawn", {
            "withdrawer": caller,
            "asset": asset,
            "amount": amount,
        })

    # ── Auction Operations ───────────────────────────────────────────────────

    @external
    def initiate_liquidation(
        self,
        caller: Address,
        user: Address,
        collateral_asset: Address,
        debt_asset: Address,
    ):
        """
        Checks if user is underwater and initiates a Dutch auction for their position.
        """
        caller.require_auth()
        self._require_initialized()

        pool = self.storage.get("lending_pool")
        
        # Verify user health factor from Lending Pool contract
        health = self.env.call(pool, "get_health_factor", [user])
        if health >= WAD:
            raise ContractError.POSITION_NOT_UNDERWATER

        auction_key = f"auction:{user}:{collateral_asset}:{debt_asset}"
        if self.storage.has(auction_key):
            raise ContractError.AUCTION_ALREADY_ACTIVE

        # Fetch position balances from Lending Pool
        balances = self.env.call(pool, "get_user_balance", [user, debt_asset])
        debt_balance = balances[1]  # Index 1 is borrowed amount

        if debt_balance == U128(0):
            raise ContractError.INVALID_PARAMETER

        # Calculate max debt to cover (Close Factor = 50%)
        debt_to_cover = (debt_balance * CLOSE_FACTOR_BPS) // BPS_DENOMINATOR

        # Start Dutch Auction parameters
        now = self.env.ledger().timestamp()
        auction = {
            "user": user,
            "collateral_asset": collateral_asset,
            "debt_asset": debt_asset,
            "debt_to_cover": debt_to_cover,
            "start_time": now,
            "active": True,
        }
        self.storage.set(auction_key, auction)

        self.env.emit_event("liquidation_initiated", {
            "user": user,
            "collateral_asset": collateral_asset,
            "debt_asset": debt_asset,
            "debt_to_cover": debt_to_cover,
            "start_time": now,
        })

    @external
    def buy_in_auction(
        self,
        caller: Address,
        user: Address,
        collateral_asset: Address,
        debt_asset: Address,
        amount_to_repay: U128,
    ):
        """
        Liquidator repays a portion of user's debt and receives collateral + Dutch bonus.
        """
        caller.require_auth()
        self._require_initialized()

        auction_key = f"auction:{user}:{collateral_asset}:{debt_asset}"
        auction = self.storage.get(auction_key, None)
        if auction is None or not auction["active"]:
            raise ContractError.AUCTION_NOT_FOUND

        if amount_to_repay > auction["debt_to_cover"]:
            raise ContractError.CLOSE_FACTOR_EXCEEDED

        # Calculate current Dutch bonus
        now = self.env.ledger().timestamp()
        elapsed = now - auction["start_time"]
        
        if elapsed >= AUCTION_DURATION:
            bonus = MAX_BONUS_BPS
        else:
            bonus_decay = ((MAX_BONUS_BPS - START_BONUS_BPS) * U128(elapsed)) // U128(AUCTION_DURATION)
            bonus = START_BONUS_BPS + bonus_decay

        # Fetch prices from pool contract
        pool = self.storage.get("lending_pool")
        coll_info = self.env.call(pool, "get_asset_info", [collateral_asset])
        debt_info = self.env.call(pool, "get_asset_info", [debt_asset])

        coll_price = coll_info["price"]
        debt_price = debt_info["price"]
        coll_decimals = coll_info["config"]["decimals"]
        debt_decimals = debt_info["config"]["decimals"]

        # Calculate value in USD
        debt_to_cover_wad = amount_to_repay * (10 ** (18 - debt_decimals))
        debt_value_usd = (debt_to_cover_wad * debt_price) // U128(100_000_000)

        # Seizure collateral value USD = debt_value * (1 + bonus)
        seize_value_usd = (debt_value_usd * (BPS_DENOMINATOR + bonus)) // BPS_DENOMINATOR
        seize_collateral_wad = (seize_value_usd * U128(100_000_000)) // coll_price
        seize_collateral = seize_collateral_wad // (10 ** (18 - coll_decimals))

        # Check user collateral availability
        user_balances = self.env.call(pool, "get_user_balance", [user, collateral_asset])
        user_collateral = user_balances[0]  # Index 0 is supplied collateral

        if seize_collateral > user_collateral:
            raise ContractError.INSUFFICIENT_COLLATERAL

        # Execute updates on Lending Pool contract
        # 1. Repay debt on behalf of borrower
        self.env.call(
            pool,
            "repay",
            [caller, debt_asset, amount_to_repay, user]
        )

        # 2. Withdraw collateral from borrower and transfer to liquidator
        # Note: Lending pool allows this engine to withdraw collateral since this is registered
        # as the official liquidation engine contract.
        self.env.call(
            pool,
            "withdraw",
            [user, collateral_asset, seize_collateral]
        )

        # Transfer seized collateral from contract to liquidator (keeper)
        self.env.transfer(self.env.current_contract(), caller, collateral_asset, seize_collateral)

        # Close auction
        self.storage.remove(auction_key)

        self.env.emit_event("liquidation_settled", {
            "user": user,
            "liquidator": caller,
            "debt_repaid": amount_to_repay,
            "collateral_seized": seize_collateral,
            "bonus_bps": bonus,
        })

    # ── Bad Debt Socialization ───────────────────────────────────────────────

    @external
    def socialize_bad_debt(
        self,
        caller: Address,
        user: Address,
        collateral_asset: Address,
        debt_asset: Address,
    ):
        """
        Socializes bad debt if auction expires and collateral value cannot cover the debt.
        Uses the Stability Pool.
        """
        caller.require_auth()
        self._require_initialized()

        auction_key = f"auction:{user}:{collateral_asset}:{debt_asset}"
        auction = self.storage.get(auction_key, None)
        if auction is None:
            raise ContractError.AUCTION_NOT_FOUND

        # Auction must have expired or price decay reached maximum
        now = self.env.ledger().timestamp()
        if now < auction["start_time"] + AUCTION_DURATION:
            raise ContractError.AUCTION_NOT_EXPIRED

        pool = self.storage.get("lending_pool")

        # Fetch remaining debt
        user_balances = self.env.call(pool, "get_user_balance", [user, debt_asset])
        debt = user_balances[1]

        # Fetch user's remaining collateral
        user_coll_balances = self.env.call(pool, "get_user_balance", [user, collateral_asset])
        collateral = user_coll_balances[0]

        # Seize all remaining collateral of borrower
        if collateral > U128(0):
            self.env.call(pool, "withdraw", [user, collateral_asset, collateral])
            # Send collateral to stability pool
            pool_collateral = self.storage.get(f"stability_pool:{collateral_asset}", U128(0))
            self.storage.set(f"stability_pool:{collateral_asset}", pool_collateral + collateral)

        # Repay debt using stability pool
        stability_balance = self.storage.get(f"stability_pool:{debt_asset}", U128(0))
        if stability_balance < debt:
            raise ContractError.STABILITY_POOL_EMPTY

        # Stability Pool repays the user's debt
        self.storage.set(f"stability_pool:{debt_asset}", stability_balance - debt)

        # Call repay from current contract address using stability pool reserves
        self.env.call(
            pool,
            "repay",
            [self.env.current_contract(), debt_asset, debt, user]
        )

        # Cleanup auction
        self.storage.remove(auction_key)

        self.env.emit_event("bad_debt_socialized", {
            "user": user,
            "socialized_debt": debt,
            "seized_collateral": collateral,
        })

    # ── View Functions ───────────────────────────────────────────────────────

    @view
    def get_auction_details(self, user: Address, collateral_asset: Address, debt_asset: Address) -> Map:
        """Returns details of an active liquidation auction."""
        auction_key = f"auction:{user}:{collateral_asset}:{debt_asset}"
        auction = self.storage.get(auction_key, None)
        if auction is None:
            raise ContractError.AUCTION_NOT_FOUND
        return auction

    @view
    def get_stability_pool_balance(self, asset: Address) -> U128:
        """Returns stability pool balance of an asset."""
        return self.storage.get(f"stability_pool:{asset}", U128(0))

    # ── Internal Helpers ─────────────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
