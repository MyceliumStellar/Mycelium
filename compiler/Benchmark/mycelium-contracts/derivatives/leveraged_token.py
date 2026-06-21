"""
Leveraged Token — Constant leverage token contract with rebalancing controls, NAV calculations, and issuance/redemption.

Mycelium Smart Contract for Stellar. Simulates a token representing a leveraged position (e.g. 3x Long) on a base asset.
Maintains target leverage by rebalancing the collateral/debt ratio based on oracle price movements.
Handles minting (issue) and burning (redeem) in exchange for stablecoin collateral, and accrues management fees.
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
    INVALID_AMOUNT = 5
    INSUFFICIENT_LIQUIDITY = 6
    ORACLE_READ_FAILED = 7
    REBALANCE_NOT_REQUIRED = 8
    ZERO_NAV = 9

@contract
class LeveragedToken:
    """
    Leveraged token manager simulating a token with constant leverage.
    We assume the contract maintains its collateral and debt in a margin pool.
    - target_leverage: e.g. 3 for 3x Long.
    - NAV starts at 10.00 scaled (10_000_000, assuming 6 decimals).
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        oracle: Address,
        collateral_token: Address,
        base_asset: Symbol,             # e.g. "XLM"
        target_leverage: U64,            # e.g. 3 for 3x
        rebalance_threshold_bps: U64,   # e.g. 500 for 5% drift from target leverage
        manager_fee_bps: U64            # e.g. 200 for 2% annual fee
    ):
        """Initialize leveraged token parameters and configuration."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("oracle", oracle)
        self.storage.set("collateral_token", collateral_token)
        self.storage.set("base_asset", base_asset)
        self.storage.set("target_leverage", target_leverage)
        self.storage.set("rebalance_threshold", rebalance_threshold_bps)
        self.storage.set("manager_fee_bps", manager_fee_bps)
        
        # Token metrics
        self.storage.set("total_supply", U128(0))
        
        # Initial NAV set to 10.0 USD (scaled by 10^6)
        self.storage.set("last_nav", U128(10_000_000))
        
        # Initial pool state
        self.storage.set("pool_collateral", U128(0)) # Total collateral in margin account
        self.storage.set("pool_debt", U128(0))       # Total debt in margin account
        
        # Track entry price of rebalancing
        initial_price = self._get_oracle_price(base_asset)
        self.storage.set("last_price", initial_price)
        self.storage.set("last_fee_timestamp", self._get_now())
        
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "base_asset": base_asset,
            "target_leverage": target_leverage
        })

    @external
    def issue(self, caller: Address, collateral_amount: U128) -> U128:
        """
        Deposit collateral token and receive newly issued leveraged tokens.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if collateral_amount == U128(0):
            raise ContractError.INVALID_AMOUNT

        # First accrue management fees
        self._accrue_fees()

        # Update NAV based on asset price changes since last check
        self._update_nav_state()

        # Transfer collateral to contract
        token = self.storage.get("collateral_token")
        contract_addr = self.env.current_contract_address()
        self.env.call(token, "transfer", caller, contract_addr, collateral_amount)

        # Get current NAV
        current_nav = self.storage.get("last_nav", U128(10_000_000))
        if current_nav == U128(0):
            raise ContractError.ZERO_NAV

        # Calculate token issuance amount
        # tokens_to_mint = collateral_amount * scale / NAV
        # Scale: 10^6
        tokens_to_mint = (collateral_amount * U128(1_000_000)) / current_nav

        # Update token supply
        supply = self.storage.get("total_supply", U128(0))
        new_supply = supply + tokens_to_mint
        self.storage.set("total_supply", new_supply)

        # Update user balance
        user_bal = self.storage.get(f"balance_{caller}", U128(0))
        self.storage.set(f"balance_{caller}", user_bal + tokens_to_mint)

        # Update pool state
        # In a leveraged position, deposit adds both collateral and leverage
        # To maintain target leverage, we must increase exposure
        target_lev = self.storage.get("target_leverage", U64(1))
        
        pool_col = self.storage.get("pool_collateral", U128(0))
        pool_debt = self.storage.get("pool_debt", U128(0))

        # Adjust leverage position: We add collateral, borrow extra (target_lev - 1) * collateral
        added_exposure = collateral_amount * U128(target_lev)
        added_debt = added_exposure - collateral_amount

        self.storage.set("pool_collateral", pool_col + added_exposure)
        self.storage.set("pool_debt", pool_debt + added_debt)

        self.env.emit_event("issued", {
            "user": caller,
            "collateral_in": collateral_amount,
            "tokens_minted": tokens_to_mint,
            "nav": current_nav
        })

        return tokens_to_mint

    @external
    def redeem(self, caller: Address, token_amount: U128) -> U128:
        """
        Burn leveraged tokens and receive stablecoin collateral in return.
        """
        caller.require_auth()
        self._require_initialized()

        user_bal = self.storage.get(f"balance_{caller}", U128(0))
        if user_bal < token_amount or token_amount == U128(0):
            raise ContractError.INVALID_AMOUNT

        # Accrue fees and update NAV
        self._accrue_fees()
        self._update_nav_state()

        current_nav = self.storage.get("last_nav", U128(10_000_000))
        if current_nav == U128(0):
            raise ContractError.ZERO_NAV

        # Calculate redemption value in collateral
        # collateral_out = token_amount * NAV / scale
        collateral_out = (token_amount * current_nav) / U128(1_000_000)

        # Burn tokens
        supply = self.storage.get("total_supply", U128(0))
        self.storage.set("total_supply", supply - token_amount)
        self.storage.set(f"balance_{caller}", user_bal - token_amount)

        # Reduce pool collateral and debt proportionately
        pool_col = self.storage.get("pool_collateral", U128(0))
        pool_debt = self.storage.get("pool_debt", U128(0))
        target_lev = self.storage.get("target_leverage", U64(1))

        # Calculate how much exposure to reduce
        exposure_reduction = collateral_out * U128(target_lev)
        debt_reduction = exposure_reduction - collateral_out

        if pool_col >= exposure_reduction:
            self.storage.set("pool_collateral", pool_col - exposure_reduction)
        else:
            self.storage.set("pool_collateral", U128(0))

        if pool_debt >= debt_reduction:
            self.storage.set("pool_debt", pool_debt - debt_reduction)
        else:
            self.storage.set("pool_debt", U128(0))

        # Transfer collateral back to user
        token = self.storage.get("collateral_token")
        contract_addr = self.env.current_contract_address()
        self.env.call(token, "transfer", contract_addr, caller, collateral_out)

        self.env.emit_event("redeemed", {
            "user": caller,
            "tokens_burned": token_amount,
            "collateral_out": collateral_out,
            "nav": current_nav
        })

        return collateral_out

    @external
    def rebalance(self, caller: Address):
        """
        Rebalance exposure back to target leverage if it drifts past threshold.
        Can be triggered by anyone if leverage is out of bounds.
        """
        self._require_initialized()
        self._require_not_paused()

        # Update NAV first
        self._accrue_fees()
        self._update_nav_state()

        # Check current leverage
        pool_col = self.storage.get("pool_collateral", U128(0))
        pool_debt = self.storage.get("pool_debt", U128(0))
        
        net_value = pool_col - pool_debt
        if net_value == U128(0):
            raise ContractError.ZERO_NAV

        # current_leverage = pool_collateral / net_value (multiplied by 10000 for bps representation)
        current_lev_bps = (pool_col * U128(10000)) / net_value

        target_lev = self.storage.get("target_leverage", U64(1))
        target_lev_bps = U128(target_lev * 10000)

        # Leverage drift
        drift = I128(0)
        if current_lev_bps >= target_lev_bps:
            drift = I128(int(current_lev_bps - target_lev_bps))
        else:
            drift = I128(int(target_lev_bps - current_lev_bps))

        threshold = self.storage.get("rebalance_threshold", U64(0))
        if drift < I128(int(threshold)):
            raise ContractError.REBALANCE_NOT_REQUIRED

        # Execute rebalance: Reset pool collateral and pool debt based on current NAV and target leverage
        new_col = net_value * U128(target_lev)
        new_debt = new_col - net_value

        self.storage.set("pool_collateral", new_col)
        self.storage.set("pool_debt", new_debt)

        # Update last price marker
        base_asset = self.storage.get("base_asset")
        price = self._get_oracle_price(base_asset)
        self.storage.set("last_price", price)

        self.env.emit_event("rebalanced", {
            "previous_leverage_bps": current_lev_bps,
            "target_leverage": target_lev,
            "new_collateral": new_col,
            "new_debt": new_debt
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause issue and redeem (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_nav(self) -> U128:
        """Get the current net asset value per token."""
        self._require_initialized()
        # Preview update to return accurate current NAV
        # Calculate NAV shift
        base_asset = self.storage.get("base_asset")
        current_price = self._get_oracle_price(base_asset)
        last_price = self.storage.get("last_price", U128(1))
        
        pool_col = self.storage.get("pool_collateral", U128(0))
        pool_debt = self.storage.get("pool_debt", U128(0))
        
        # PnL from price change
        price_shift_bps = (I128(int(current_price)) - I128(int(last_price))) * I128(10000) / I128(int(last_price))
        
        target_lev = self.storage.get("target_leverage", U64(1))
        pnl = (I128(int(pool_col)) * price_shift_bps * I128(int(target_lev))) / I128(10000)

        # Net Asset Value
        net_val_i128 = I128(int(pool_col)) - I128(int(pool_debt)) + pnl
        if net_val_i128 < I128(0):
            net_val_i128 = I128(0)

        supply = self.storage.get("total_supply", U128(0))
        if supply == U128(0):
            return U128(10_000_000) # Initial default

        return U128(int(net_val_i128)) * U128(1_000_000) / supply

    @view
    def balance_of(self, account: Address) -> U128:
        """Query leveraged token balance for an account."""
        return self.storage.get(f"balance_{account}", U128(0))

    @view
    def get_token_state(self) -> Map:
        """Query leveraged token metrics."""
        res = Map(self.env)
        res.set("total_supply", self.storage.get("total_supply"))
        res.set("last_nav", self.storage.get("last_nav"))
        res.set("pool_collateral", self.storage.get("pool_collateral"))
        res.set("pool_debt", self.storage.get("pool_debt"))
        res.set("target_leverage", self.storage.get("target_leverage"))
        res.set("base_asset", self.storage.get("base_asset"))
        return res

    # --- INTERNAL HELPERS ---

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_not_paused(self):
        if self.storage.get("paused", False):
            raise ContractError.PAUSED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _get_now(self) -> U64:
        return self.env.ledger_timestamp()

    def _get_oracle_price(self, asset: Symbol) -> U128:
        """Call external Oracle to fetch current asset price."""
        oracle = self.storage.get("oracle")
        try:
            return self.env.call(oracle, "get_price", asset)
        except Exception:
            raise ContractError.ORACLE_READ_FAILED

    def _accrue_fees(self):
        """Accrue manager fee since last accrual (2% annual fee deducted from collateral)."""
        now = self._get_now()
        last_fee_time = self.storage.get("last_fee_timestamp", U64(0))
        if now <= last_fee_time:
            return

        elapsed = now - last_fee_time
        fee_bps = self.storage.get("manager_fee_bps", U64(0))
        pool_col = self.storage.get("pool_collateral", U128(0))

        if pool_col == U128(0) or fee_bps == U64(0):
            self.storage.set("last_fee_timestamp", now)
            return

        # Fee = Pool Collateral * fee_bps * elapsed / (10000 * 31,536,000)
        fee = (pool_col * U128(fee_bps) * U128(elapsed)) / U128(315_360_000_000)

        if fee > U128(0):
            # Reduce collateral, send to admin as fee
            if pool_col >= fee:
                self.storage.set("pool_collateral", pool_col - fee)
            else:
                self.storage.set("pool_collateral", U128(0))

            token = self.storage.get("collateral_token")
            contract_addr = self.env.current_contract_address()
            admin = self.storage.get("admin")
            self.env.call(token, "transfer", contract_addr, admin, fee)

        self.storage.set("last_fee_timestamp", now)

    def _update_nav_state(self):
        """Update NAV metrics based on underlying exposure shift since last price check."""
        base_asset = self.storage.get("base_asset")
        current_price = self._get_oracle_price(base_asset)
        last_price = self.storage.get("last_price", U128(1))
        
        if current_price == last_price:
            return

        pool_col = self.storage.get("pool_collateral", U128(0))
        pool_debt = self.storage.get("pool_debt", U128(0))
        target_lev = self.storage.get("target_leverage", U64(1))

        # Price diff percentage: bps = (current - last) * 10000 / last
        price_diff = I128(int(current_price)) - I128(int(last_price))
        price_change_bps = (price_diff * I128(10000)) / I128(int(last_price))

        # Collateral value adjustment based on leverage: Collateral * price_change * leverage
        pnl = (I128(int(pool_col)) * price_change_bps * I128(int(target_lev))) / I128(10000)

        adjusted_collateral = I128(int(pool_col)) + pnl
        if adjusted_collateral < I128(0):
            adjusted_collateral = I128(0)

        self.storage.set("pool_collateral", U128(int(adjusted_collateral)))
        self.storage.set("last_price", current_price)

        # Update NAV = (adjusted_collateral - debt) / supply
        supply = self.storage.get("total_supply", U128(0))
        net_val = I128(int(adjusted_collateral)) - I128(int(pool_debt))
        
        if net_val < I128(0) or supply == U128(0):
            nav = U128(0)
        else:
            # Scaled by 10^6
            nav = (U128(int(net_val)) * U128(1_000_000)) / supply

        self.storage.set("last_nav", nav)
