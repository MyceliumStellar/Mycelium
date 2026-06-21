"""
Multi-Strategy Vault — Manages capital allocation across multiple yield strategies.

Mycelium Smart Contract for Stellar
Implements a strategy registry, target allocation weights, automated rebalancing rules,
harvesting of yields, and charging of both performance and management fees.
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
    STRATEGY_NOT_FOUND = 5
    STRATEGY_LIMIT_REACHED = 6
    WEIGHTS_MUST_TOTAL_10000 = 7
    INSUFFICIENT_FUNDS = 8
    STRATEGY_ALREADY_EXISTS = 9


@contract
class MultiStrategyVault:
    """
    Allocates underlying assets across registered strategies based on configurable
    basis-point weights. Supports periodic rebalancing and fee accrual.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        keeper: Address,
        underlying: Address,
        fee_recipient: Address,
        performance_fee_bps: U64,
        management_fee_bps: U64,  # Annual fee in bps, e.g. 200 bps = 2%
    ):
        """Initialize the multi-strategy vault contract."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if performance_fee_bps > 5000 or management_fee_bps > 1000:
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("admin", admin)
        self.storage.set("keeper", keeper)
        self.storage.set("underlying", underlying)
        self.storage.set("fee_recipient", fee_recipient)
        self.storage.set("perf_fee_bps", performance_fee_bps)
        self.storage.set("mgt_fee_bps", management_fee_bps)

        self.storage.set("last_fee_time", self.env.ledger().timestamp())
        self.storage.set("idle_assets", U128(0))
        
        # We store strategies as a Map or list keys. Let's track count.
        self.storage.set("strategy_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "underlying": underlying,
            "perf_fee_bps": performance_fee_bps,
            "mgt_fee_bps": management_fee_bps,
        })

    @external
    def add_strategy(self, admin: Address, strategy: Address, weight_bps: U64):
        """Register a new strategy with a specific weight in basis points."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        if self.storage.get(f"strategy:exists:{strategy}", False):
            raise ContractError.STRATEGY_ALREADY_EXISTS

        count = self.storage.get("strategy_count", U64(0))
        if count >= 10:  # Max 10 strategies to prevent out of gas during loop
            raise ContractError.STRATEGY_LIMIT_REACHED

        self.storage.set(f"strategy:exists:{strategy}", True)
        self.storage.set(f"strategy:weight:{strategy}", weight_bps)
        self.storage.set(f"strategy:allocated:{strategy}", U128(0))
        
        # Store index for reference
        self.storage.set(f"strategy:index:{count}", strategy)
        self.storage.set("strategy_count", count + U64(1))

        self.env.emit_event("strategy_added", {
            "strategy": strategy,
            "weight": weight_bps,
        })

    @external
    def set_weights(self, admin: Address, strategies: Vec, weights: Vec):
        """Update allocation weights for registered strategies."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        # Accrue fees before shifting allocations
        self._accrue_management_fee()

        if len(strategies) != len(weights):
            raise ContractError.INVALID_PARAMETERS

        total_weight = U64(0)
        # Verify strategy registry and calculate total
        for i in range(len(strategies)):
            strat = strategies[i]
            w = weights[i]
            if not self.storage.get(f"strategy:exists:{strat}", False):
                raise ContractError.STRATEGY_NOT_FOUND
            total_weight = total_weight + w

        # Total weights must equal 10000 bps (100%)
        if total_weight != 10000:
            raise ContractError.WEIGHTS_MUST_TOTAL_10000

        # Update weights
        for i in range(len(strategies)):
            strat = strategies[i]
            w = weights[i]
            self.storage.set(f"strategy:weight:{strat}", w)

        self.env.emit_event("weights_updated", {
            "strategies": strategies,
            "weights": weights,
        })

    @external
    def deposit(self, caller: Address, amount: U128):
        """Deposit underlying assets into the vault (stored as idle first)."""
        caller.require_auth()
        self._require_initialized()

        if amount == 0:
            raise ContractError.INVALID_PARAMETERS

        self._accrue_management_fee()

        underlying = self.storage.get("underlying")
        self.env.transfer(underlying, caller, self.env.current_contract(), amount)

        idle = self.storage.get("idle_assets", U128(0))
        self.storage.set("idle_assets", idle + amount)

        self.env.emit_event("deposited", {"caller": caller, "amount": amount})

    @external
    def withdraw(self, caller: Address, amount: U128):
        """Withdraw underlying assets from the vault (pulls from strategies if needed)."""
        caller.require_auth()
        self._require_initialized()

        self._accrue_management_fee()

        total = self.get_total_assets()
        if amount > total:
            raise ContractError.INSUFFICIENT_FUNDS

        idle = self.storage.get("idle_assets", U128(0))
        if idle < amount:
            needed = amount - idle
            self._withdraw_from_strategies(needed)
            idle = self.storage.get("idle_assets", U128(0))

        self.storage.set("idle_assets", idle - amount)

        underlying = self.storage.get("underlying")
        self.env.transfer(underlying, self.env.current_contract(), caller, amount)

        self.env.emit_event("withdrawn", {"caller": caller, "amount": amount})

    @external
    def rebalance(self, caller: Address):
        """Rebalance capital across strategies based on their target weights."""
        caller.require_auth()
        self._require_initialized()
        self._require_keeper_or_admin(caller)

        self._accrue_management_fee()

        total_assets = self.get_total_assets()
        if total_assets == 0:
            return

        count = self.storage.get("strategy_count", U64(0))
        underlying = self.storage.get("underlying")

        # Step 1: Withdraw from strategies that are over-allocated
        for i in range(count):
            strat = self.storage.get(f"strategy:index:{i}")
            weight = self.storage.get(f"strategy:weight:{strat}", U64(0))
            allocated = self.storage.get(f"strategy:allocated:{strat}", U128(0))
            
            target = (total_assets * U128(weight)) // U128(10000)
            
            if allocated > target:
                excess = allocated - target
                # Pull excess underlying from strategy
                self.env.transfer(underlying, strat, self.env.current_contract(), excess)
                self.storage.set(f"strategy:allocated:{strat}", target)
                
                idle = self.storage.get("idle_assets", U128(0))
                self.storage.set("idle_assets", idle + excess)

        # Step 2: Deposit into strategies that are under-allocated
        for i in range(count):
            strat = self.storage.get(f"strategy:index:{i}")
            weight = self.storage.get(f"strategy:weight:{strat}", U64(0))
            allocated = self.storage.get(f"strategy:allocated:{strat}", U128(0))
            
            target = (total_assets * U128(weight)) // U128(10000)
            
            if allocated < target:
                deficit = target - allocated
                idle = self.storage.get("idle_assets", U128(0))
                
                # Check if we have enough idle assets to allocate (handling rounding errors)
                amount_to_allocate = deficit
                if amount_to_allocate > idle:
                    amount_to_allocate = idle

                if amount_to_allocate > 0:
                    self.env.transfer(underlying, self.env.current_contract(), strat, amount_to_allocate)
                    self.storage.set(f"strategy:allocated:{strat}", allocated + amount_to_allocate)
                    self.storage.set("idle_assets", idle - amount_to_allocate)

        self.env.emit_event("rebalanced", {"total_assets": total_assets})

    @external
    def harvest(self, caller: Address, strategy: Address, profit: U128):
        """Harvest yield generated by a specific strategy, taking performance fee."""
        caller.require_auth()
        self._require_initialized()
        self._require_keeper_or_admin(caller)

        if not self.storage.get(f"strategy:exists:{strategy}", False):
            raise ContractError.STRATEGY_NOT_FOUND

        self._accrue_management_fee()

        if profit == 0:
            return

        # Calculate performance fee
        perf_bps = self.storage.get("perf_fee_bps")
        perf_fee = (profit * U128(perf_bps)) // U128(10000)
        net_profit = profit - perf_fee

        underlying = self.storage.get("underlying")
        fee_recipient = self.storage.get("fee_recipient")

        # Pull profit from strategy
        self.env.transfer(underlying, strategy, self.env.current_contract(), profit)

        # Send performance fee to fee recipient
        if perf_fee > 0:
            self.env.transfer(underlying, self.env.current_contract(), fee_recipient, perf_fee)

        # Rest of net profit is added to vault's idle assets
        idle = self.storage.get("idle_assets", U128(0))
        self.storage.set("idle_assets", idle + net_profit)

        self.env.emit_event("harvested", {
            "strategy": strategy,
            "profit": profit,
            "net_profit": net_profit,
            "performance_fee": perf_fee,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_total_assets(self) -> U128:
        """Return the sum of idle assets plus all strategy allocations."""
        idle = self.storage.get("idle_assets", U128(0))
        allocated_total = U128(0)
        
        count = self.storage.get("strategy_count", U64(0))
        for i in range(count):
            strat = self.storage.get(f"strategy:index:{i}")
            allocated = self.storage.get(f"strategy:allocated:{strat}", U128(0))
            allocated_total = allocated_total + allocated

        return idle + allocated_total

    @view
    def get_strategy_info(self, strategy: Address) -> Map:
        """Get weights and allocation info for a strategy."""
        if not self.storage.get(f"strategy:exists:{strategy}", False):
            raise ContractError.STRATEGY_NOT_FOUND
        return {
            "weight": self.storage.get(f"strategy:weight:{strategy}"),
            "allocated": self.storage.get(f"strategy:allocated:{strategy}"),
        }

    @view
    def get_idle_assets(self) -> U128:
        """Get current idle underlying assets inside the vault."""
        return self.storage.get("idle_assets", U128(0))

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_keeper_or_admin(self, caller: Address):
        admin = self.storage.get("admin")
        keeper = self.storage.get("keeper")
        if caller != admin and caller != keeper:
            raise ContractError.UNAUTHORIZED

    def _accrue_management_fee(self):
        current_time = self.env.ledger().timestamp()
        last_fee_time = self.storage.get("last_fee_time")
        if current_time <= last_fee_time:
            return

        total_assets = self.get_total_assets()
        if total_assets == 0:
            self.storage.set("last_fee_time", current_time)
            return

        mgt_bps = self.storage.get("mgt_fee_bps")
        if mgt_bps == 0:
            self.storage.set("last_fee_time", current_time)
            return

        time_elapsed = current_time - last_fee_time
        # Management fee calculation: assets * (mgt_bps / 10000) * (elapsed / seconds_in_year)
        # Year = 31,536,000 seconds
        annual_fee = (total_assets * U128(mgt_bps)) // U128(10000)
        fee_amount = (annual_fee * U128(time_elapsed)) // U128(31536000)

        if fee_amount > 0:
            idle = self.storage.get("idle_assets", U128(0))
            underlying = self.storage.get("underlying")
            fee_recipient = self.storage.get("fee_recipient")

            if idle >= fee_amount:
                self.storage.set("idle_assets", idle - fee_amount)
                self.env.transfer(underlying, self.env.current_contract(), fee_recipient, fee_amount)
            else:
                # If idle is not enough, pull what is available first, then update
                # In a real vault, we can withdraw from strategies or queue the fee deficit.
                # Here we just withdraw from strategies to cover it.
                needed = fee_amount - idle
                self._withdraw_from_strategies(needed)
                
                # Deduct full fee amount now that we have idle
                current_idle = self.storage.get("idle_assets", U128(0))
                self.storage.set("idle_assets", current_idle - fee_amount)
                self.env.transfer(underlying, self.env.current_contract(), fee_recipient, fee_amount)

        self.storage.set("last_fee_time", current_time)

    def _withdraw_from_strategies(self, amount_needed: U128):
        count = self.storage.get("strategy_count", U64(0))
        underlying = self.storage.get("underlying")
        remaining = amount_needed

        # Iterate strategies to pull assets until amount_needed is satisfied
        for i in range(count):
            if remaining == 0:
                break
            
            # Start from the last strategy added to minimize disruptiveness
            idx = count - U64(1) - i
            strat = self.storage.get(f"strategy:index:{idx}")
            allocated = self.storage.get(f"strategy:allocated:{strat}", U128(0))

            if allocated > 0:
                to_withdraw = remaining
                if to_withdraw > allocated:
                    to_withdraw = allocated

                self.env.transfer(underlying, strat, self.env.current_contract(), to_withdraw)
                self.storage.set(f"strategy:allocated:{strat}", allocated - to_withdraw)
                
                idle = self.storage.get("idle_assets", U128(0))
                self.storage.set("idle_assets", idle + to_withdraw)
                remaining = remaining - to_withdraw
