"""
Perpetual Futures — Leverage positions tracking, funding rates, and liquidations.

Mycelium Smart Contract for Stellar. Tracks long/short futures positions, locks collateral
as margin, applies accumulated funding rate updates, verifies oracle prices, and
triggers liquidation if margin falls below the maintenance threshold.
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
    INVALID_LEVERAGE = 5
    INSUFFICIENT_MARGIN = 6
    NO_OPEN_POSITION = 7
    POSITION_ALREADY_EXISTS = 8
    LIQUIDATION_NOT_ELIGIBLE = 9
    ORACLE_READ_FAILED = 10
    INVALID_MARKET = 11

@contract
class PerpetualFutures:
    """
    Perpetual futures contract with margin account tracking and liquidations.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        oracle: Address,
        margin_token: Address,
        max_leverage: U64,             # e.g. 20 for 20x leverage
        maintenance_margin_bps: U64,   # e.g. 500 for 5% maintenance margin
        liquidation_fee_bps: U64       # e.g. 100 for 1% liquidation fee
    ):
        """Initialize perpetual market configurations, oracle, and margin rules."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("oracle", oracle)
        self.storage.set("margin_token", margin_token)
        self.storage.set("max_leverage", max_leverage)
        self.storage.set("maintenance_margin_bps", maintenance_margin_bps)
        self.storage.set("liquidation_fee_bps", liquidation_fee_bps)
        
        # Cumulative funding rate index. Multiplied by 10^12 for precision.
        self.storage.set("cum_funding_rate", I128(0))
        self.storage.set("last_funding_time", self._get_now())

        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "oracle": oracle,
            "margin_token": margin_token,
            "max_leverage": max_leverage
        })

    @external
    def open_position(
        self,
        caller: Address,
        market: Symbol,
        margin: U128,
        leverage: U64,
        is_long: Bool
    ):
        """
        Open a leveraged futures position by locking margin collateral.
        - leverage: e.g. 10 for 10x
        - is_long: True for long, False for short
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        # Check existing position
        if self.storage.get(f"pos_active_{caller}_{market}", False):
            raise ContractError.POSITION_ALREADY_EXISTS

        # Validate leverage
        max_lev = self.storage.get("max_leverage", U64(1))
        if leverage == U64(0) or leverage > max_lev:
            raise ContractError.INVALID_LEVERAGE

        # Check minimum margin (e.g. 10 tokens minimum)
        if margin < U128(10_000_000): # Assuming 7 decimals, 10 tokens
            raise ContractError.INSUFFICIENT_MARGIN

        # Get current asset price from oracle
        price = self._get_oracle_price(market)

        # Calculate position size = margin * leverage
        size = margin * U128(leverage)

        # Transfer margin tokens to perp contract
        token = self.storage.get("margin_token")
        contract_addr = self.env.current_contract_address()
        self.env.call(token, "transfer", caller, contract_addr, margin)

        # Record position details
        self.storage.set(f"pos_active_{caller}_{market}", True)
        self.storage.set(f"pos_margin_{caller}_{market}", margin)
        self.storage.set(f"pos_size_{caller}_{market}", size)
        self.storage.set(f"pos_entry_price_{caller}_{market}", price)
        self.storage.set(f"pos_is_long_{caller}_{market}", is_long)
        
        # Save funding rate checkpoint when position opened
        cum_funding = self.storage.get("cum_funding_rate", I128(0))
        self.storage.set(f"pos_funding_checkpoint_{caller}_{market}", cum_funding)

        self.env.emit_event("position_opened", {
            "user": caller,
            "market": market,
            "margin": margin,
            "size": size,
            "entry_price": price,
            "is_long": is_long
        })

    @external
    def close_position(self, caller: Address, market: Symbol):
        """
        Close an open position, settling PnL and funding fees.
        """
        caller.require_auth()
        self._require_initialized()

        if not self.storage.get(f"pos_active_{caller}_{market}", False):
            raise ContractError.NO_OPEN_POSITION

        # Accrue pending funding rate updates before calculating PnL
        self._accrue_funding_pool()

        # Retrieve position details
        margin = self.storage.get(f"pos_margin_{caller}_{market}", U128(0))
        size = self.storage.get(f"pos_size_{caller}_{market}", U128(0))
        entry_price = self.storage.get(f"pos_entry_price_{caller}_{market}", U128(0))
        is_long = self.storage.get(f"pos_is_long_{caller}_{market}", False)
        funding_checkpoint = self.storage.get(f"pos_funding_checkpoint_{caller}_{market}", I128(0))

        # Get current price
        current_price = self._get_oracle_price(market)

        # 1. Calculate PnL (in margin token)
        # PnL_long = size * (current_price - entry_price) / entry_price
        # PnL_short = size * (entry_price - current_price) / entry_price
        pnl = I128(0)
        price_diff = I128(int(current_price)) - I128(int(entry_price))
        
        if is_long:
            pnl = (I128(int(size)) * price_diff) / I128(int(entry_price))
        else:
            pnl = (I128(int(size)) * (-price_diff)) / I128(int(entry_price))

        # 2. Calculate funding fee accrued
        cum_funding = self.storage.get("cum_funding_rate", I128(0))
        funding_diff = cum_funding - funding_checkpoint
        # Funding fee = size * funding_diff / multiplier
        # Long pays funding to short if funding_diff > 0
        # If is_long, funding fee is deducted. If short, it is added.
        funding_accrued = (I128(int(size)) * funding_diff) / I128(1_000_000_000_000)

        # Net margin payout = margin + PnL - funding_fee (if long pays) or + funding_fee (if short receives)
        # Note: If is_long, funding_accrued represents what the long pays, so we subtract it.
        # If short, they receive it, so we add it.
        if is_long:
            net_payout_i128 = I128(int(margin)) + pnl - funding_accrued
        else:
            net_payout_i128 = I128(int(margin)) + pnl + funding_accrued

        # Prevent negative balance
        if net_payout_i128 < I128(0):
            net_payout = U128(0)
        else:
            net_payout = U128(int(net_payout_i128))

        # Clean position
        self._clear_position(caller, market)

        # Transfer margin token back to user
        token = self.storage.get("margin_token")
        contract_addr = self.env.current_contract_address()
        if net_payout > U128(0):
            self.env.call(token, "transfer", contract_addr, caller, net_payout)

        self.env.emit_event("position_closed", {
            "user": caller,
            "market": market,
            "pnl": pnl,
            "funding_fee": funding_accrued,
            "payout": net_payout
        })

    @external
    def update_funding_rate(self, caller: Address, funding_rate_bps: I128):
        """
        Periodically adjust the funding rate based on market premium (Admin or Keeper only).
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        # Accrue first with previous rate
        self._accrue_funding_pool()

        # Update funding rate bps in storage
        self.storage.set("funding_rate_bps", funding_rate_bps)

        self.env.emit_event("funding_rate_updated", {
            "new_rate_bps": funding_rate_bps
        })

    @external
    def liquidate_position(self, caller: Address, user: Address, market: Symbol):
        """
        Liquidate a position if its remaining margin falls below the maintenance threshold.
        The liquidator receives a liquidation reward fee.
        """
        caller.require_auth()
        self._require_initialized()

        if not self.storage.get(f"pos_active_{user}_{market}", False):
            raise ContractError.NO_OPEN_POSITION

        # Accrue funding
        self._accrue_funding_pool()

        # Check eligibility
        is_eligible, net_margin = self._check_liquidation_eligibility(user, market)
        if not is_eligible:
            raise ContractError.LIQUIDATION_NOT_ELIGIBLE

        # Retrieve variables
        size = self.storage.get(f"pos_size_{user}_{market}", U128(0))
        liq_fee_bps = self.storage.get("liquidation_fee_bps", U64(0))

        # Calculate reward to liquidator: size * liq_fee_bps / 10000
        reward = (size * U128(liq_fee_bps)) / U128(10000)
        if reward > net_margin:
            reward = net_margin

        treasury_payout = net_margin - reward

        # Clear position
        self._clear_position(user, market)

        token = self.storage.get("margin_token")
        contract_addr = self.env.current_contract_address()

        # Pay liquidator
        if reward > U128(0):
            self.env.call(token, "transfer", contract_addr, caller, reward)

        # Pay remaining to admin/treasury
        if treasury_payout > U128(0):
            admin = self.storage.get("admin")
            self.env.call(token, "transfer", contract_addr, admin, treasury_payout)

        self.env.emit_event("position_liquidated", {
            "liquidated_user": user,
            "liquidator": caller,
            "market": market,
            "reward": reward,
            "treasury_payout": treasury_payout
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause the perp trading functions (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_position(self, user: Address, market: Symbol) -> Map:
        """Fetch position details including current unrealized PnL."""
        res = Map(self.env)
        active = self.storage.get(f"pos_active_{user}_{market}", False)
        if active:
            margin = self.storage.get(f"pos_margin_{user}_{market}", U128(0))
            size = self.storage.get(f"pos_size_{user}_{market}", U128(0))
            entry_price = self.storage.get(f"pos_entry_price_{user}_{market}", U128(0))
            is_long = self.storage.get(f"pos_is_long_{user}_{market}", False)
            funding_checkpoint = self.storage.get(f"pos_funding_checkpoint_{user}_{market}", I128(0))

            price = self._get_oracle_price(market)
            price_diff = I128(int(price)) - I128(int(entry_price))

            pnl = I128(0)
            if is_long:
                pnl = (I128(int(size)) * price_diff) / I128(int(entry_price))
            else:
                pnl = (I128(int(size)) * (-price_diff)) / I128(int(entry_price))

            res.set("active", True)
            res.set("margin", margin)
            res.set("size", size)
            res.set("entry_price", entry_price)
            res.set("is_long", is_long)
            res.set("unrealized_pnl", pnl)
        else:
            res.set("active", False)
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

    def _get_oracle_price(self, market: Symbol) -> U128:
        """Call the price feed oracle to fetch current asset price."""
        oracle = self.storage.get("oracle")
        # Expected signature on oracle: get_price(market: Symbol) -> U128
        try:
            return self.env.call(oracle, "get_price", market)
        except Exception:
            raise ContractError.ORACLE_READ_FAILED

    def _accrue_funding_pool(self):
        """Update the cumulative funding rate accumulator based on elapsed time."""
        now = self._get_now()
        last_time = self.storage.get("last_funding_time", U64(0))
        if now <= last_time:
            return

        elapsed = now - last_time
        funding_rate_bps = self.storage.get("funding_rate_bps", I128(0))
        cum_funding = self.storage.get("cum_funding_rate", I128(0))

        # Funding index increment = (funding_rate_bps * elapsed * 10^12) / (365 * 24 * 3600 * 10000)
        # Simulating per-second funding accrual. Let's scale and divide.
        # Year in seconds = 31,536,000. Rate is in basis points (/10000).
        # We scale by multiplier 1_000_000_000_000 (10^12)
        yearly_denominator = I128(31_536_000 * 10000)
        accrual = (funding_rate_bps * I128(int(elapsed)) * I128(1_000_000_000_000)) / yearly_denominator

        self.storage.set("cum_funding_rate", cum_funding + accrual)
        self.storage.set("last_funding_time", now)

    def _check_liquidation_eligibility(self, user: Address, market: Symbol) -> (Bool, U128):
        """Calculate current position health and check if it falls under liquidation threshold."""
        margin = self.storage.get(f"pos_margin_{user}_{market}", U128(0))
        size = self.storage.get(f"pos_size_{user}_{market}", U128(0))
        entry_price = self.storage.get(f"pos_entry_price_{user}_{market}", U128(0))
        is_long = self.storage.get(f"pos_is_long_{user}_{market}", False)
        funding_checkpoint = self.storage.get(f"pos_funding_checkpoint_{user}_{market}", I128(0))

        price = self._get_oracle_price(market)
        price_diff = I128(int(price)) - I128(int(entry_price))

        # PnL
        pnl = I128(0)
        if is_long:
            pnl = (I128(int(size)) * price_diff) / I128(int(entry_price))
        else:
            pnl = (I128(int(size)) * (-price_diff)) / I128(int(entry_price))

        # Funding
        cum_funding = self.storage.get("cum_funding_rate", I128(0))
        funding_diff = cum_funding - funding_checkpoint
        funding_accrued = (I128(int(size)) * funding_diff) / I128(1_000_000_000_000)

        # Net remaining margin
        if is_long:
            net_margin_i128 = I128(int(margin)) + pnl - funding_accrued
        else:
            net_margin_i128 = I128(int(margin)) + pnl + funding_accrued

        if net_margin_i128 < I128(0):
            return True, U128(0)

        net_margin = U128(int(net_margin_i128))

        # Maintenance Margin required: size * maintenance_margin_bps / 10000
        maintenance_bps = self.storage.get("maintenance_margin_bps", U64(0))
        required_maintenance = (size * U128(maintenance_bps)) / U128(10000)

        # If net margin is below required maintenance margin, it can be liquidated
        return (net_margin < required_maintenance), net_margin

    def _clear_position(self, user: Address, market: Symbol):
        """Remove position records from storage."""
        self.storage.remove(f"pos_active_{user}_{market}")
        self.storage.remove(f"pos_margin_{user}_{market}")
        self.storage.remove(f"pos_size_{user}_{market}")
        self.storage.remove(f"pos_entry_price_{user}_{market}")
        self.storage.remove(f"pos_is_long_{user}_{market}")
        self.storage.remove(f"pos_funding_checkpoint_{user}_{market}")
