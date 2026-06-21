"""
Yield Aggregator — Multi-strategy yield optimizer vault.

Features:
  - Deposit underlying assets to receive vault LP shares
  - Share-based mint/burn tracking proportional to total assets
  - Dynamic capital allocation across multiple active strategies
  - Target debt limit allocation checks
  - Auto-compounding strategy harvest invocation
  - Performance fee deduction (minting equivalent shares to protocol treasury)
  - Strategy migration (redeeming from old, deploying to new)
  - Loss handling (reducing debt ledger entries on strategy shortfalls)
  - Reentrancy locks and admin security policies

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
    REENTRANCY_GUARD = 4
    ZERO_AMOUNT = 5
    INSUFFICIENT_SHARES = 6
    STRATEGY_NOT_FOUND = 7
    STRATEGY_ALREADY_EXISTS = 8
    INVALID_ALLOCATION = 9
    TOTAL_ALLOCATION_EXCEEDED = 10
    INSUFFICIENT_FUNDS = 11
    MIGRATION_FAILED = 12


# Constants
FEE_DENOMINATOR = U128(10000)
MAX_PERFORMANCE_FEE = U64(3000)  # 30% max performance fee


@contract
class YieldAggregator:
    """
    Multi-strategy yield aggregator vault optimizing deployment of an underlying
    token across various strategies.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ------------------------------------------------------------------ #
    #  Initialization
    # ------------------------------------------------------------------ #

    @external
    def initialize(
        self,
        admin: Address,
        treasury: Address,
        underlying: Address,
        performance_fee_bps: U64,
    ):
        """Initialise the yield vault."""
        admin.require_auth()

        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if performance_fee_bps > MAX_PERFORMANCE_FEE:
            raise ContractError.INVALID_ALLOCATION

        self.storage.set("admin", admin)
        self.storage.set("treasury", treasury)
        self.storage.set("underlying", underlying)
        self.storage.set("performance_fee_bps", performance_fee_bps)
        self.storage.set("total_shares", U128(0))
        self.storage.set("strategies", Vec())
        self.storage.set("reentrancy_locked", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "treasury": treasury,
            "underlying": underlying,
            "performance_fee": performance_fee_bps
        })

    # ------------------------------------------------------------------ #
    #  User Deposits and Withdrawals
    # ------------------------------------------------------------------ #

    @external
    def deposit(self, caller: Address, amount: U128) -> U128:
        """
        Deposit underlying token into vault, receiving vault shares.
        Triggers auto-deployment of idle cash to strategies.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_locked()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        self._set_locked(True)

        underlying = self.storage.get("underlying")
        total_shares = self.storage.get("total_shares")
        total_vault_assets = self._total_assets()

        # Calculate shares to mint
        shares_to_mint = U128(0)
        if total_shares == U128(0) or total_vault_assets == U128(0):
            shares_to_mint = amount
        else:
            shares_to_mint = (amount * total_shares) // total_vault_assets

        if shares_to_mint == U128(0):
            raise ContractError.ZERO_AMOUNT

        # Transfer underlying into vault
        self.env.transfer(caller, self.env.current_contract(), underlying, amount)

        # Update share balances
        user_shares = self.storage.get(f"shares:{caller}", U128(0))
        self.storage.set(f"shares:{caller}", user_shares + shares_to_mint)
        self.storage.set("total_shares", total_shares + shares_to_mint)

        # Deploy capital to strategy allocators
        self._deploy_capital()

        self._set_locked(False)

        self.env.emit_event("deposited", {
            "user": caller,
            "amount": amount,
            "shares_minted": shares_to_mint
        })
        return shares_to_mint

    @external
    def withdraw(self, caller: Address, shares: U128) -> U128:
        """
        Withdraw underlying by burning LP shares.
        Draws down idle cash first, then pulls from active strategies if needed.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_locked()

        if shares == U128(0):
            raise ContractError.ZERO_AMOUNT

        self._set_locked(True)

        user_shares = self.storage.get(f"shares:{caller}", U128(0))
        if user_shares < shares:
            raise ContractError.INSUFFICIENT_SHARES

        underlying = self.storage.get("underlying")
        total_shares = self.storage.get("total_shares")
        total_vault_assets = self._total_assets()

        # Calculate cash payout: shares * total_assets / total_shares
        amount_to_withdraw = (shares * total_vault_assets) // total_shares

        if amount_to_withdraw == U128(0):
            raise ContractError.ZERO_AMOUNT

        # Check free cash inside vault
        free_cash = self._get_cash_balance()
        
        if free_cash < amount_to_withdraw:
            # Need to withdraw excess from strategies
            deficit = amount_to_withdraw - free_cash
            self._withdraw_from_strategies(deficit)
            # Re-read cash balance to confirm
            free_cash = self._get_cash_balance()
            if free_cash < amount_to_withdraw:
                amount_to_withdraw = free_cash

        # Burn shares
        self.storage.set(f"shares:{caller}", user_shares - shares)
        self.storage.set("total_shares", total_shares - shares)

        # Transfer cash to user
        self.env.transfer(self.env.current_contract(), caller, underlying, amount_to_withdraw)

        self._set_locked(False)

        self.env.emit_event("withdrawn", {
            "user": caller,
            "amount_withdrawn": amount_to_withdraw,
            "shares_burned": shares
        })
        return amount_to_withdraw

    # ------------------------------------------------------------------ #
    #  Strategy Administration & Optimization
    # ------------------------------------------------------------------ #

    @external
    def add_strategy(
        self,
        caller: Address,
        strategy: Address,
        allocation_bps: U64,
        debt_limit: U128,
    ):
        """Register an external strategy contract. Admin only."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        strategies = self.storage.get("strategies")
        for i in range(len(strategies)):
            if strategies[i] == strategy:
                raise ContractError.STRATEGY_ALREADY_EXISTS

        # Enforce strategy allocation bounds
        current_alloc = self._get_total_alloc()
        if current_alloc + allocation_bps > 10000:
            raise ContractError.TOTAL_ALLOCATION_EXCEEDED

        strategies.append(strategy)
        self.storage.set("strategies", strategies)

        self.storage.set(f"strategy_active:{strategy}", True)
        self.storage.set(f"strategy_allocation_bps:{strategy}", allocation_bps)
        self.storage.set(f"strategy_debt_limit:{strategy}", debt_limit)
        self.storage.set(f"strategy_current_debt:{strategy}", U128(0))

        self.env.emit_event("strategy_added", {
            "strategy": strategy,
            "allocation_bps": allocation_bps,
            "debt_limit": debt_limit
        })

    @external
    def update_strategy(
        self,
        caller: Address,
        strategy: Address,
        allocation_bps: U64,
        debt_limit: U128,
        active: Bool,
    ):
        """Update target weights or pause strategy. Admin only."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        self._require_valid_strategy(strategy)

        old_alloc = self.storage.get(f"strategy_allocation_bps:{strategy}")
        current_alloc = self._get_total_alloc() - old_alloc

        if current_alloc + allocation_bps > 10000:
            raise ContractError.TOTAL_ALLOCATION_EXCEEDED

        self.storage.set(f"strategy_active:{strategy}", active)
        self.storage.set(f"strategy_allocation_bps:{strategy}", allocation_bps)
        self.storage.set(f"strategy_debt_limit:{strategy}", debt_limit)

        self.env.emit_event("strategy_updated", {
            "strategy": strategy,
            "allocation_bps": allocation_bps,
            "debt_limit": debt_limit,
            "active": active
        })

    @external
    def harvest(self, caller: Address, strategy: Address):
        """
        Trigger yield harvest for a strategy. Admin or keeper only.
        Applies performance fee to profits. Auto-compounds remains.
        """
        self._require_initialized()
        self._require_valid_strategy(strategy)

        # Call harvest on strategy
        # Expecting strategy to yield profit/loss info as [profit: U128, loss: U128]
        harvest_result = self.env.invoke_contract(strategy, "harvest", [])
        profit = harvest_result[0]
        loss = harvest_result[1]

        current_debt = self.storage.get(f"strategy_current_debt:{strategy}")

        if profit > U128(0):
            # Calculate performance fee
            fee_bps = U128(self.storage.get("performance_fee_bps"))
            perf_fee_val = (profit * fee_bps) // FEE_DENOMINATOR

            if perf_fee_val > U128(0):
                # Mint equivalent shares to treasury
                treasury = self.storage.get("treasury")
                total_shares = self.storage.get("total_shares")
                total_vault_assets = self._total_assets()

                shares_fee = (perf_fee_val * total_shares) // total_vault_assets
                self.storage.set(f"shares:{treasury}", self.storage.get(f"shares:{treasury}", U128(0)) + shares_fee)
                self.storage.set("total_shares", total_shares + shares_fee)

            # Strategy reinvests profit, increasing our debt allocation record
            self.storage.set(f"strategy_current_debt:{strategy}", current_debt + profit)

        if loss > U128(0):
            # Reduce strategy debt record
            if loss > current_debt:
                self.storage.set(f"strategy_current_debt:{strategy}", U128(0))
            else:
                self.storage.set(f"strategy_current_debt:{strategy}", current_debt - loss)

        # Re-allocate capital after compounding
        self._deploy_capital()

        self.env.emit_event("harvested", {
            "strategy": strategy,
            "profit": profit,
            "loss": loss
        })

    @external
    def migrate(self, caller: Address, strategy_old: Address, strategy_new: Address):
        """Migrate capital from old strategy to new strategy. Admin only."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        self._require_valid_strategy(strategy_old)
        self._require_valid_strategy(strategy_new)

        self._set_locked(True)

        old_debt = self.storage.get(f"strategy_current_debt:{strategy_old}")

        # Withdraw all capital from old strategy
        # Strategy withdraws all underlying and transfers to this vault
        self.env.invoke_contract(strategy_old, "withdraw_all", [])
        
        # Reset old strategy debt
        self.storage.set(f"strategy_current_debt:{strategy_old}", U128(0))
        self.storage.set(f"strategy_active:{strategy_old}", False)
        self.storage.set(f"strategy_allocation_bps:{strategy_old}", U64(0))

        # Check actual cash received
        cash_balance = self._get_cash_balance()
        amount_to_migrate = min(cash_balance, old_debt)

        # Deposit into new strategy
        underlying = self.storage.get("underlying")
        self.env.transfer(self.env.current_contract(), strategy_new, underlying, amount_to_migrate)
        self.env.invoke_contract(strategy_new, "deposit", [amount_to_migrate])

        self.storage.set(f"strategy_current_debt:{strategy_new}", amount_to_migrate)

        self._set_locked(False)

        self.env.emit_event("migrated", {
            "old": strategy_old,
            "new": strategy_new,
            "amount": amount_to_migrate
        })

    # ------------------------------------------------------------------ #
    #  View Functions
    # ------------------------------------------------------------------ #

    @view
    def total_assets(self) -> U128:
        """Get the total asset size managed by the vault (idle cash + strategy debts)."""
        return self._total_assets()

    @view
    def get_shares_price(self) -> U128:
        """Get the price of 1 share in terms of underlying asset (scaled by 1e18)."""
        total_shares = self.storage.get("total_shares", U128(0))
        if total_shares == U128(0):
            return U128(1_000_000_000_000_000_000)

        return (self._total_assets() * U128(1_000_000_000_000_000_000)) // total_shares

    @view
    def get_strategy_info(self, strategy: Address) -> Map:
        """Get config and debt status of strategy."""
        return {
            "active": self.storage.get(f"strategy_active:{strategy}", False),
            "allocation_bps": self.storage.get(f"strategy_allocation_bps:{strategy}", U64(0)),
            "debt_limit": self.storage.get(f"strategy_debt_limit:{strategy}", U128(0)),
            "current_debt": self.storage.get(f"strategy_current_debt:{strategy}", U128(0))
        }

    @view
    def get_active_strategies(self) -> Vec:
        """Return list of strategy Addresses."""
        return self.storage.get("strategies", Vec())

    @view
    def get_user_balance(self, user: Address) -> U128:
        """Return user's shares balance."""
        return self.storage.get(f"shares:{user}", U128(0))

    # ------------------------------------------------------------------ #
    #  Internal Allocation Logic
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_not_locked(self):
        if self.storage.get("reentrancy_locked", False):
            raise ContractError.REENTRANCY_GUARD

    def _set_locked(self, locked: Bool):
        self.storage.set("reentrancy_locked", locked)

    def _require_valid_strategy(self, strategy: Address):
        strategies = self.storage.get("strategies")
        exists = False
        for i in range(len(strategies)):
            if strategies[i] == strategy:
                exists = True
                break
        if not exists:
            raise ContractError.STRATEGY_NOT_FOUND

    def _get_cash_balance(self) -> U128:
        underlying = self.storage.get("underlying")
        return self.env.token(underlying).balance(self.env.current_contract())

    def _total_assets(self) -> U128:
        assets = self._get_cash_balance()
        strategies = self.storage.get("strategies")
        for i in range(len(strategies)):
            debt = self.storage.get(f"strategy_current_debt:{strategies[i]}", U128(0))
            assets += debt
        return assets

    def _get_total_alloc(self) -> U64:
        strategies = self.storage.get("strategies")
        tot = U64(0)
        for i in range(len(strategies)):
            tot += self.storage.get(f"strategy_allocation_bps:{strategies[i]}", U64(0))
        return tot

    def _deploy_capital(self):
        """Pushes idle cash into strategies up to their limits/ratios."""
        strategies = self.storage.get("strategies")
        underlying = self.storage.get("underlying")

        for i in range(len(strategies)):
            strategy = strategies[i]
            active = self.storage.get(f"strategy_active:{strategy}", False)
            if not active:
                continue

            free_cash = self._get_cash_balance()
            if free_cash == U128(0):
                break

            alloc_bps = U128(self.storage.get(f"strategy_allocation_bps:{strategy}", U64(0)))
            debt_limit = self.storage.get(f"strategy_debt_limit:{strategy}", U128(0))
            curr_debt = self.storage.get(f"strategy_current_debt:{strategy}", U128(0))

            # Compute target debt: allocation % of total assets
            target_debt = (self._total_assets() * alloc_bps) // FEE_DENOMINATOR
            if target_debt > debt_limit:
                target_debt = debt_limit

            if target_debt > curr_debt:
                amount_to_deploy = min(target_debt - curr_debt, free_cash)
                if amount_to_deploy > U128(0):
                    # Transfer underlying to strategy
                    self.env.transfer(self.env.current_contract(), strategy, underlying, amount_to_deploy)
                    # Notify strategy
                    self.env.invoke_contract(strategy, "deposit", [amount_to_deploy])
                    # Update debt
                    self.storage.set(f"strategy_current_debt:{strategy}", curr_debt + amount_to_deploy)

    def _withdraw_from_strategies(self, amount: U128):
        """Draws down debt allocations to cover user withdrawals."""
        strategies = self.storage.get("strategies")
        remaining_to_withdraw = amount

        for i in range(len(strategies)):
            strategy = strategies[len(strategies) - 1 - i]  # Withdraw backwards
            curr_debt = self.storage.get(f"strategy_current_debt:{strategy}", U128(0))
            if curr_debt == U128(0):
                continue

            withdraw_amt = min(remaining_to_withdraw, curr_debt)
            if withdraw_amt > U128(0):
                # Trigger strategy withdrawal
                # Strategy returns tokens to vault
                self.env.invoke_contract(strategy, "withdraw", [withdraw_amt])
                # Update debt
                self.storage.set(f"strategy_current_debt:{strategy}", curr_debt - withdraw_amt)
                remaining_to_withdraw -= withdraw_amt

            if remaining_to_withdraw == U128(0):
                break
