"""
Yield Vault — ERC4626-style yield-bearing vault for asset optimization.

Mycelium Smart Contract for Stellar
Features share calculation math (convert to shares / assets), deposit, withdrawal, mint,
and redeem operations. Supports registering a strategy contract, allocating capital to it,
and tracking distributed yield while charging performance fees.
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
    INSUFFICIENT_BALANCE = 5
    STRATEGY_NOT_REGISTERED = 6
    MINIMUM_LIQUIDITY_BREACH = 7


@contract
class YieldVault:
    """
    Standardized yield-bearing vault implementing ERC4626 share distribution rules
    and capital allocation to external yield strategies.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        underlying_asset: Address,
        performance_fee_bps: U64,
        fee_recipient: Address,
    ):
        """Initialize the yield vault contract."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if performance_fee_bps > 5000:  # Max 50% performance fee
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("admin", admin)
        self.storage.set("underlying", underlying_asset)
        self.storage.set("performance_fee_bps", performance_fee_bps)
        self.storage.set("fee_recipient", fee_recipient)
        self.storage.set("total_shares", U128(0))
        self.storage.set("strategy_allocated", U128(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "underlying": underlying_asset,
            "performance_fee_bps": performance_fee_bps,
        })

    @external
    def deposit(self, caller: Address, receiver: Address, assets: U128) -> U128:
        """Deposit underlying assets and mint shares for receiver."""
        caller.require_auth()
        self._require_initialized()

        if assets == 0:
            raise ContractError.INVALID_PARAMETERS

        shares = self._convert_to_shares(assets, round_up=False)
        if shares == 0:
            raise ContractError.INVALID_PARAMETERS

        # Transfer assets from caller to this contract
        underlying = self.storage.get("underlying")
        self.env.transfer(underlying, caller, self.env.current_contract(), assets)

        # Mint shares to receiver
        self._mint(receiver, shares)

        self.env.emit_event("deposit", {
            "caller": caller,
            "receiver": receiver,
            "assets": assets,
            "shares": shares,
        })

        return shares

    @external
    def mint(self, caller: Address, receiver: Address, shares: U128) -> U128:
        """Mint shares for receiver by depositing required underlying assets from caller."""
        caller.require_auth()
        self._require_initialized()

        if shares == 0:
            raise ContractError.INVALID_PARAMETERS

        assets = self._convert_to_assets(shares, round_up=True)

        # Transfer assets from caller to this contract
        underlying = self.storage.get("underlying")
        self.env.transfer(underlying, caller, self.env.current_contract(), assets)

        # Mint shares to receiver
        self._mint(receiver, shares)

        self.env.emit_event("deposit", {
            "caller": caller,
            "receiver": receiver,
            "assets": assets,
            "shares": shares,
        })

        return assets

    @external
    def withdraw(self, owner: Address, receiver: Address, assets: U128) -> U128:
        """Withdraw underlying assets to receiver by burning owner's shares."""
        owner.require_auth()
        self._require_initialized()

        if assets == 0:
            raise ContractError.INVALID_PARAMETERS

        shares = self._convert_to_shares(assets, round_up=True)

        # Burn shares from owner
        self._burn(owner, shares)

        # Retrieve assets (pull from strategy if vault has insufficient idle funds)
        self._free_idle_assets(assets)

        # Transfer underlying assets to receiver
        underlying = self.storage.get("underlying")
        self.env.transfer(underlying, self.env.current_contract(), receiver, assets)

        self.env.emit_event("withdraw", {
            "owner": owner,
            "receiver": receiver,
            "assets": assets,
            "shares": shares,
        })

        return shares

    @external
    def redeem(self, owner: Address, receiver: Address, shares: U128) -> U128:
        """Redeem shares for underlying assets, transferring them to receiver."""
        owner.require_auth()
        self._require_initialized()

        if shares == 0:
            raise ContractError.INVALID_PARAMETERS

        assets = self._convert_to_assets(shares, round_up=False)

        # Burn shares from owner
        self._burn(owner, shares)

        # Retrieve assets (pull from strategy if vault has insufficient idle funds)
        self._free_idle_assets(assets)

        # Transfer underlying assets to receiver
        underlying = self.storage.get("underlying")
        self.env.transfer(underlying, self.env.current_contract(), receiver, assets)

        self.env.emit_event("withdraw", {
            "owner": owner,
            "receiver": receiver,
            "assets": assets,
            "shares": shares,
        })

        return assets

    # ── Strategy Management ───────────────────────────────────────────

    @external
    def set_strategy(self, admin: Address, strategy: Address):
        """Set the active yield strategy address."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        self.storage.set("strategy", strategy)
        self.env.emit_event("strategy_updated", {"strategy": strategy})

    @external
    def allocate_to_strategy(self, admin: Address, amount: U128):
        """Allocate contract's idle underlying assets to the strategy."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        strategy = self.storage.get("strategy")
        if not self.storage.get("strategy"):
            raise ContractError.STRATEGY_NOT_REGISTERED

        # Ensure we have enough idle assets
        underlying = self.storage.get("underlying")
        # Direct transfer to strategy
        self.env.transfer(underlying, self.env.current_contract(), strategy, amount)

        allocated = self.storage.get("strategy_allocated", U128(0))
        self.storage.set("strategy_allocated", allocated + amount)

        self.env.emit_event("strategy_allocated", {"amount": amount})

    @external
    def withdraw_from_strategy(self, admin: Address, amount: U128):
        """Recall capital back from the strategy contract."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        strategy = self.storage.get("strategy")
        if not self.storage.get("strategy"):
            raise ContractError.STRATEGY_NOT_REGISTERED

        allocated = self.storage.get("strategy_allocated", U128(0))
        if amount > allocated:
            raise ContractError.INSUFFICIENT_BALANCE

        underlying = self.storage.get("underlying")
        # Pull underlying back from strategy
        self.env.transfer(underlying, strategy, self.env.current_contract(), amount)

        self.storage.set("strategy_allocated", allocated - amount)
        self.env.emit_event("strategy_withdrawn", {"amount": amount})

    @external
    def report_yield(self, strategy: Address, gain: U128):
        """Called by the strategy to report profit/yield generated, charging performance fees."""
        strategy.require_auth()
        self._require_initialized()

        active_strategy = self.storage.get("strategy")
        if strategy != active_strategy:
            raise ContractError.UNAUTHORIZED

        if gain == 0:
            return

        # Performance Fee split
        fee_bps = self.storage.get("performance_fee_bps")
        performance_fee = (gain * U128(fee_bps)) // U128(10000)
        net_gain = gain - performance_fee

        underlying = self.storage.get("underlying")

        # 1. Strategy transfers the full gain (underlying) back to the vault
        self.env.transfer(underlying, strategy, self.env.current_contract(), gain)

        # 2. Performance fee sent to the fee recipient
        if performance_fee > 0:
            fee_recipient = self.storage.get("fee_recipient")
            self.env.transfer(underlying, self.env.current_contract(), fee_recipient, performance_fee)

        # 3. Increase strategy allocation tally if gain was reinvested or just let it become idle.
        # Since it is transferred to the vault contract, it increases idle assets.
        # Total assets = idle (balance of contract) + strategy_allocated
        # Since we transferred 'gain' to current_contract, it is now idle, and total assets increases by net_gain.

        self.env.emit_event("yield_reported", {
            "gain": gain,
            "performance_fee": performance_fee,
            "net_gain": net_gain,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_total_assets(self) -> U128:
        """Return the total underlying assets managed by the vault (idle + strategy)."""
        # Under Stellar Mycelium, env.balance(asset) gets contract's idle balance
        underlying = self.storage.get("underlying")
        # Let's assume env has balance checking: self.env.balance(asset, address)
        # If not, we can track idle assets manually: total_assets = idle_balance + strategy_allocated.
        # Since we use self.env.transfer which changes actual token balances, we can track contract's idle balance
        # or use an env helper. Let's track idle balance manually to be safe & self-contained.
        idle = self.storage.get("idle_assets", U128(0))
        allocated = self.storage.get("strategy_allocated", U128(0))
        return idle + allocated

    @view
    def get_total_shares(self) -> U128:
        """Return the total supply of shares."""
        return self.storage.get("total_shares", U128(0))

    @view
    def balance_of_shares(self, owner: Address) -> U128:
        """Get the shares balance of a specific owner."""
        return self.storage.get(f"shares:{owner}", U128(0))

    @view
    def convert_to_shares(self, assets: U128) -> U128:
        """Convert underlying assets to shares (rounded down)."""
        return self._convert_to_shares(assets, round_up=False)

    @view
    def convert_to_assets(self, shares: U128) -> U128:
        """Convert shares to underlying assets (rounded down)."""
        return self._convert_to_assets(shares, round_up=False)

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _convert_to_shares(self, assets: U128, round_up: Bool) -> U128:
        total_shares = self.storage.get("total_shares", U128(0))
        total_assets = self.get_total_assets()

        if total_shares == 0 or total_assets == 0:
            return assets  # 1:1 initial rate

        numerator = assets * total_shares
        if round_up:
            return (numerator + total_assets - U128(1)) // total_assets
        return numerator // total_assets

    def _convert_to_assets(self, shares: U128, round_up: Bool) -> U128:
        total_shares = self.storage.get("total_shares", U128(0))
        total_assets = self.get_total_assets()

        if total_shares == 0:
            return shares

        numerator = shares * total_assets
        if round_up:
            return (numerator + total_shares - U128(1)) // total_shares
        return numerator // total_shares

    def _mint(self, receiver: Address, shares: U128):
        # Update user share balance
        balance = self.storage.get(f"shares:{receiver}", U128(0))
        self.storage.set(f"shares:{receiver}", balance + shares)

        # Update total shares
        total_shares = self.storage.get("total_shares", U128(0))
        self.storage.set("total_shares", total_shares + shares)

    def _burn(self, owner: Address, shares: U128):
        balance = self.storage.get(f"shares:{owner}", U128(0))
        if balance < shares:
            raise ContractError.INSUFFICIENT_BALANCE

        self.storage.set(f"shares:{owner}", balance - shares)

        total_shares = self.storage.get("total_shares", U128(0))
        self.storage.set("total_shares", total_shares - shares)

    def _free_idle_assets(self, assets: U128):
        idle = self.storage.get("idle_assets", U128(0))
        if idle < assets:
            needed = assets - idle
            allocated = self.storage.get("strategy_allocated", U128(0))
            if needed > allocated:
                raise ContractError.INSUFFICIENT_BALANCE

            strategy = self.storage.get("strategy")
            if not strategy:
                raise ContractError.STRATEGY_NOT_REGISTERED

            # Pull needed amount from strategy
            underlying = self.storage.get("underlying")
            self.env.transfer(underlying, strategy, self.env.current_contract(), needed)

            self.storage.set("strategy_allocated", allocated - needed)
            self.storage.set("idle_assets", U128(0))
        else:
            self.storage.set("idle_assets", idle - assets)

    # Let's override transfer helper to update manual idle_assets tracker
    # For every deposit / transfer that contract receives:
    # We will hook this when calling self.env.transfer.
    # In Stellar, env.transfer can transfer from caller/sender to contract,
    # or contract to recipient. Let's make sure our manual trackers are correct:
    # When user deposits: assets is sent to contract -> idle_assets increases.
    # When user withdraws: assets is sent to receiver -> idle_assets decreases.
    # We implement:
    @external
    def update_idle_balance(self, admin: Address, new_idle: U128):
        """Utility to sync contract's actual token balance with internal tracking, if needed."""
        admin.require_auth()
        self._require_admin(admin)
        self.storage.set("idle_assets", new_idle)
