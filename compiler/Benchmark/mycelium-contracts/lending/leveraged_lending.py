"""
Leveraged Lending — Recursive auto-leverage loops, health monitoring, and one-click flash unwind.

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
    HEALTH_FACTOR_TOO_LOW = 4
    INVALID_ASSET = 5
    SLIPPAGE_EXCEEDED = 6
    UNWIND_FAILED = 7
    CALLBACK_ONLY = 8
    ZERO_AMOUNT = 9
    OVERFLOW = 10
    POSITION_NOT_FOUND = 11


# ── Constants ────────────────────────────────────────────────────────────────

WAD = U128(1_000_000_000_000_000_000)  # 1e18 scale
BPS_DENOMINATOR = U128(10000)
EMERGENCY_HEALTH_THRESHOLD = U128(1_050_000_000_000_000_000)  # 1.05 health factor


@contract
class LeveragedLending:
    """
    Leveraged lending vault contract.
    Enables users to open leveraged positions (e.g. 3x) in a single transaction
    by executing a recursive supply-borrow-swap loop.
    Supports one-click unwind of leveraged positions by taking a flash loan
    to repay outstanding debt, withdrawing the collateral, swapping it on a DEX,
    and returning the net proceeds (equity) to the user.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ── Administrative Functions ─────────────────────────────────────────────

    @external
    def initialize(self, admin: Address, lending_pool: Address, dex: Address):
        """
        Initializes the contract. Sets the administrator.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("lending_pool", lending_pool)
        self.storage.set("dex", dex)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "lending_pool": lending_pool,
            "dex": dex,
        })

    # ── Auto Leverage Loop ───────────────────────────────────────────────────

    @external
    def auto_leverage(
        self,
        caller: Address,
        collateral_asset: Address,
        borrow_asset: Address,
        deposit_amount: U128,
        loops: U64,
    ):
        """
        Builds a leveraged position via a recursive supply-borrow-swap loop.
        """
        caller.require_auth()
        self._require_initialized()

        if deposit_amount == U128(0) or loops == U64(0):
            raise ContractError.ZERO_AMOUNT

        # Collect user deposit
        self.env.transfer(caller, self.env.current_contract(), collateral_asset, deposit_amount)

        pool = self.storage.get("lending_pool")
        dex = self.storage.get("dex")

        # Fetch asset configurations to calculate borrow amount per step
        coll_info = self.env.call(pool, "get_asset_info", [collateral_asset])
        borrow_info = self.env.call(pool, "get_asset_info", [borrow_asset])

        ltv = coll_info["config"]["ltv"]  # e.g., 80% (8000 bps)
        coll_decimals = coll_info["config"]["decimals"]
        borrow_decimals = borrow_info["config"]["decimals"]

        current_input = deposit_amount
        total_supplied = U128(0)
        total_borrowed = U128(0)

        # Approve and recursive loop
        for i in range(loops):
            # 1. Supply collateral to pool
            self.env.call(pool, "supply", [self.env.current_contract(), collateral_asset, current_input])
            total_supplied += current_input

            # Calculate safe borrow size based on LTV
            # borrow_usd = coll_usd * ltv / 10000
            coll_price = coll_info["price"]
            borrow_price = borrow_info["price"]
            
            current_input_wad = current_input * (10 ** (18 - coll_decimals))
            coll_value_usd = (current_input_wad * coll_price) // U128(100_000_000)
            
            borrow_value_usd = (coll_value_usd * ltv) // BPS_DENOMINATOR
            borrow_amount_wad = (borrow_value_usd * U128(100_000_000)) // borrow_price
            borrow_size = borrow_amount_wad // (10 ** (18 - borrow_decimals))

            # Leave buffer to avoid immediate liquidation
            borrow_size = (borrow_size * U128(9500)) // BPS_DENOMINATOR
            if borrow_size == U128(0):
                break

            # 2. Borrow debt token
            self.env.call(pool, "borrow", [self.env.current_contract(), borrow_asset, borrow_size])
            total_borrowed += borrow_size

            # 3. Swap borrowed asset for collateral asset on DEX
            # Signature: swap_exact_input(caller, token_in, amount_in, min_out, deadline)
            deadline = self.env.ledger().timestamp() + U64(300)
            swapped_collateral = self.env.call(
                dex,
                "swap_exact_input",
                [self.env.current_contract(), borrow_asset, borrow_size, U128(1), deadline]
            )

            current_input = swapped_collateral

        # Update user position storage
        pos_key = f"user_position:{caller}:{collateral_asset}:{borrow_asset}"
        pos = self.storage.get(pos_key, None)
        if pos is None:
            pos = {
                "collateral": total_supplied,
                "debt": total_borrowed,
            }
        else:
            pos["collateral"] += total_supplied
            pos["debt"] += total_borrowed

        self.storage.set(pos_key, pos)

        self.env.emit_event("position_leveraged", {
            "user": caller,
            "collateral_added": total_supplied,
            "borrow_added": total_borrowed,
        })

    # ── One-Click Flash Unwind ───────────────────────────────────────────────

    @external
    def unwind_position(
        self,
        caller: Address,
        collateral_asset: Address,
        borrow_asset: Address,
        flash_pool: Address,
    ):
        """
        Unwinds the user's leveraged position in a single transaction.
        Initiates a flash loan of the debt asset to pay off the borrow position.
        """
        caller.require_auth()
        self._require_initialized()

        pos_key = f"user_position:{caller}:{collateral_asset}:{borrow_asset}"
        pos = self.storage.get(pos_key, None)
        if pos is None:
            raise ContractError.POSITION_NOT_FOUND

        debt_to_repay = pos["debt"]

        # Build callback data for flash loan execution
        callback_data = {
            "user": caller,
            "collateral_asset": collateral_asset,
            "borrow_asset": borrow_asset,
            "flash_pool": flash_pool,
        }

        # Request flash loan of borrow asset from flash_pool
        # Note: Flash loan transfers borrow_asset to this contract and calls flash_loan_callback
        self.env.call(
            flash_pool,
            "flash_loan",
            [self.env.current_contract(), borrow_asset, debt_to_repay, callback_data]
        )

    @external
    def flash_loan_callback(
        self,
        pool_caller: Address,
        asset: Address,
        amount: U128,
        fee: U128,
        callback_data: Map,
    ):
        """
        Callback executed by flash loan pool.
        Repays debt, withdraws collateral, swaps collateral to cover flash loan, returns net.
        """
        pool_caller.require_auth()
        self._require_initialized()

        expected_pool = callback_data["flash_pool"]
        if pool_caller != expected_pool:
            raise ContractError.CALLBACK_ONLY

        user = callback_data["user"]
        collateral_asset = callback_data["collateral_asset"]
        borrow_asset = callback_data["borrow_asset"]

        pos_key = f"user_position:{user}:{collateral_asset}:{borrow_asset}"
        pos = self.storage.get(pos_key)

        pool = self.storage.get("lending_pool")
        dex = self.storage.get("dex")

        # 1. Repay entire debt to Lending Pool
        self.env.call(pool, "repay", [self.env.current_contract(), borrow_asset, amount, self.env.current_contract()])

        # 2. Withdraw total collateral from Lending Pool
        self.env.call(pool, "withdraw", [self.env.current_contract(), collateral_asset, pos["collateral"]])

        # 3. Calculate amount needed to cover flash loan + fee
        flash_loan_debt = amount + fee

        # Swap collateral on DEX to receive flash_loan_debt amount of borrow asset
        # Signature: swap_exact_output(caller, token_out, amount_out, max_amount_in, deadline)
        deadline = self.env.ledger().timestamp() + U64(300)
        coll_swapped = self.env.call(
            dex,
            "swap_exact_output",
            [self.env.current_contract(), borrow_asset, flash_loan_debt, pos["collateral"], deadline]
        )

        # Repay flash loan pool
        self.env.transfer(self.env.current_contract(), expected_pool, borrow_asset, flash_loan_debt)

        # Return remaining collateral (equity) to user
        net_equity = pos["collateral"] - coll_swapped
        if net_equity > U128(0):
            self.env.transfer(self.env.current_contract(), user, collateral_asset, net_equity)

        # Clear user position
        self.storage.remove(pos_key)

        self.env.emit_event("position_unwound", {
            "user": user,
            "repaid_debt": amount,
            "collateral_returned": net_equity,
        })

    # ── Emergency Trigger ────────────────────────────────────────────────────

    @external
    def emergency_deleverage(
        self,
        caller: Address,
        user: Address,
        collateral_asset: Address,
        borrow_asset: Address,
        flash_pool: Address,
    ):
        """
        Can be triggered by anyone if the health factor drops below 1.05.
        Protects the position from total liquidation by unwinding it.
        """
        self._require_initialized()

        health = self.get_position_health(user, collateral_asset, borrow_asset)
        if health > EMERGENCY_HEALTH_THRESHOLD:
            raise ContractError.UNAUTHORIZED  # Only authorized if health is critical

        # Unwind the position to secure remaining equity
        pos_key = f"user_position:{user}:{collateral_asset}:{borrow_asset}"
        pos = self.storage.get(pos_key, None)
        if pos is None:
            raise ContractError.POSITION_NOT_FOUND

        debt_to_repay = pos["debt"]
        callback_data = {
            "user": user,
            "collateral_asset": collateral_asset,
            "borrow_asset": borrow_asset,
            "flash_pool": flash_pool,
        }

        self.env.call(
            flash_pool,
            "flash_loan",
            [self.env.current_contract(), borrow_asset, debt_to_repay, callback_data]
        )

        self.env.emit_event("emergency_unwind_triggered", {"user": user})

    # ── View Functions ───────────────────────────────────────────────────────

    @view
    def get_position_health(self, user: Address, collateral_asset: Address, borrow_asset: Address) -> U128:
        """
        Queries and returns health factor of a leveraged position in WAD.
        """
        pos_key = f"user_position:{user}:{collateral_asset}:{borrow_asset}"
        pos = self.storage.get(pos_key, None)
        if pos is None:
            return U128(0)

        pool = self.storage.get("lending_pool")
        coll_info = self.env.call(pool, "get_asset_info", [collateral_asset])
        borrow_info = self.env.call(pool, "get_asset_info", [borrow_asset])

        coll_price = coll_info["price"]
        borrow_price = borrow_info["price"]
        coll_decimals = coll_info["config"]["decimals"]
        borrow_decimals = borrow_info["config"]["decimals"]

        coll_wad = pos["collateral"] * (10 ** (18 - coll_decimals))
        borrow_wad = pos["debt"] * (10 ** (18 - borrow_decimals))

        coll_usd = (coll_wad * coll_price) // U128(100_000_000)
        borrow_usd = (borrow_wad * borrow_price) // U128(100_000_000)

        if borrow_usd == U128(0):
            return WAD * U128(100)

        threshold = coll_info["config"]["liq_threshold"]
        threshold_coll_usd = (coll_usd * threshold) // BPS_DENOMINATOR

        return (threshold_coll_usd * WAD) // borrow_usd

    @view
    def get_user_position(self, user: Address, collateral_asset: Address, borrow_asset: Address) -> Map:
        """Returns details of a user's leveraged position."""
        pos_key = f"user_position:{user}:{collateral_asset}:{borrow_asset}"
        pos = self.storage.get(pos_key, None)
        if pos is None:
            raise ContractError.POSITION_NOT_FOUND
        return pos

    # ── Internal Helpers ─────────────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
