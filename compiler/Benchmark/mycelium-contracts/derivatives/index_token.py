"""
Index Token — Multi-asset basket weights, rebalancing controls, NAV calculations, and management fee accrual.

Mycelium Smart Contract for Stellar. Tracks a token representing a weighted basket of underlying assets.
Supports issuing index tokens by depositing the proportional basket of assets, and redemption by withdrawing the basket.
Allows the admin to adjust target weights, and includes a virtual rebalancing mechanism to realign asset allocations.
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
    INVALID_BASKET = 5
    INVALID_AMOUNT = 6
    ORACLE_READ_FAILED = 7
    BASKET_MISMATCH = 8
    ZERO_NAV = 9
    WEIGHTS_NOT_SUM_100 = 10

@contract
class IndexToken:
    """
    Index Token Contract managing a basket of assets.
    Weights are represented in basis points (10000 = 100%).
    NAV is scaled by 10^6.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        oracle: Address,
        assets: Vec,        # Vec of Address (underlying token contracts)
        weights: Vec,       # Vec of U64 (bps, e.g. 5000, 3000, 2000)
        manager_fee_bps: U64
    ):
        """Initialize index token basket composition and weights."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if len(assets) != len(weights) or len(assets) == 0:
            raise ContractError.INVALID_BASKET

        # Verify weights sum up to 10000 (100%)
        total_weight = U64(0)
        for i in range(len(weights)):
            total_weight += weights.get(i)

        if total_weight != U64(10000):
            raise ContractError.WEIGHTS_NOT_SUM_100

        self.storage.set("admin", admin)
        self.storage.set("oracle", oracle)
        self.storage.set("assets", assets)
        self.storage.set("manager_fee_bps", manager_fee_bps)
        self.storage.set("total_supply", U128(0))
        self.storage.set("last_fee_timestamp", self._get_now())
        self.storage.set("paused", False)
        
        # Save weights in mapping
        for i in range(len(assets)):
            asset = assets.get(i)
            weight = weights.get(i)
            self.storage.set(f"weight_{asset}", weight)

        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "oracle": oracle,
            "assets_count": len(assets)
        })

    @external
    def mint(self, caller: Address, index_token_amount: U128):
        """
        Mint index tokens by depositing the required proportional amount of underlying basket assets.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if index_token_amount == U128(0):
            raise ContractError.INVALID_AMOUNT

        self._accrue_fees()

        # Retrieve assets list
        assets = self.storage.get("assets")
        oracle = self.storage.get("oracle")
        contract_addr = self.env.current_contract_address()

        # Calculate NAV of the index
        nav = self._calculate_nav()

        # Transfer proportional assets from user to contract
        # For each asset, the quantity required = index_token_amount * weight_i * NAV / (price_i * 10000)
        # Assuming asset prices are scaled, we maintain integer precision.
        for i in range(len(assets)):
            asset = assets.get(i)
            weight = self.storage.get(f"weight_{asset}", U64(0))
            
            # Fetch asset price from oracle
            price = self._get_oracle_price(asset)
            
            # quantity = (index_token_amount * weight * NAV) / (price * 10000)
            quantity = (index_token_amount * U128(weight) * nav) / (price * U128(10000))
            
            if quantity > U128(0):
                self.env.call(asset, "transfer", caller, contract_addr, quantity)

        # Update supply and user balance
        supply = self.storage.get("total_supply", U128(0))
        self.storage.set("total_supply", supply + index_token_amount)

        user_bal = self.storage.get(f"balance_{caller}", U128(0))
        self.storage.set(f"balance_{caller}", user_bal + index_token_amount)

        self.env.emit_event("minted", {
            "user": caller,
            "amount": index_token_amount,
            "nav": nav
        })

    @external
    def redeem(self, caller: Address, index_token_amount: U128):
        """
        Redeem index tokens to receive the proportional underlying basket assets.
        """
        caller.require_auth()
        self._require_initialized()

        user_bal = self.storage.get(f"balance_{caller}", U128(0))
        if user_bal < index_token_amount or index_token_amount == U128(0):
            raise ContractError.INVALID_AMOUNT

        self._accrue_fees()

        assets = self.storage.get("assets")
        contract_addr = self.env.current_contract_address()
        nav = self._calculate_nav()

        # Burn tokens first
        supply = self.storage.get("total_supply", U128(0))
        self.storage.set("total_supply", supply - index_token_amount)
        self.storage.set(f"balance_{caller}", user_bal - index_token_amount)

        # Transfer proportional assets from contract to user
        for i in range(len(assets)):
            asset = assets.get(i)
            weight = self.storage.get(f"weight_{asset}", U64(0))
            price = self._get_oracle_price(asset)

            # quantity = (index_token_amount * weight * NAV) / (price * 10000)
            quantity = (index_token_amount * U128(weight) * nav) / (price * U128(10000))

            if quantity > U128(0):
                self.env.call(asset, "transfer", contract_addr, caller, quantity)

        self.env.emit_event("redeemed", {
            "user": caller,
            "amount": index_token_amount,
            "nav": nav
        })

    @external
    def rebalance_basket(self, caller: Address, new_weights: Vec):
        """
        Adjust target weights of the basket (admin only).
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        assets = self.storage.get("assets")
        if len(new_weights) != len(assets):
            raise ContractError.INVALID_BASKET

        # Verify new weights sum to 10000
        total_weight = U64(0)
        for i in range(len(new_weights)):
            total_weight += new_weights.get(i)

        if total_weight != U64(10000):
            raise ContractError.WEIGHTS_NOT_SUM_100

        # Update weights
        for i in range(len(assets)):
            asset = assets.get(i)
            weight = new_weights.get(i)
            self.storage.set(f"weight_{asset}", weight)

        self.env.emit_event("basket_rebalanced", {
            "weights": new_weights
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause minting and redemption (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_nav(self) -> U128:
        """Query current NAV per index token (scaled by 10^6)."""
        self._require_initialized()
        return self._calculate_nav()

    @view
    def balance_of(self, account: Address) -> U128:
        """Query index token balance of an account."""
        return self.storage.get(f"balance_{account}", U128(0))

    @view
    def get_basket_details(self) -> Map:
        """Query basket assets and weights."""
        res = Map(self.env)
        assets = self.storage.get("assets")
        if assets is not None:
            for i in range(len(assets)):
                asset = assets.get(i)
                weight = self.storage.get(f"weight_{asset}")
                res.set(f"asset_{i}", asset)
                res.set(f"weight_{i}", weight)
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

    def _get_oracle_price(self, asset: Address) -> U128:
        """Call external Oracle to fetch asset price."""
        oracle = self.storage.get("oracle")
        try:
            # Expected signature on oracle: get_token_price(token: Address) -> U128
            return self.env.call(oracle, "get_token_price", asset)
        except Exception:
            raise ContractError.ORACLE_READ_FAILED

    def _calculate_nav(self) -> U128:
        """
        Calculate Net Asset Value (NAV) per index token based on underlying token balances and prices.
        NAV = sum(asset_balance_i * price_i) / total_supply
        If total supply is 0, default NAV is 10.0 scaled (10_000_000).
        """
        supply = self.storage.get("total_supply", U128(0))
        if supply == U128(0):
            return U128(10_000_000)

        assets = self.storage.get("assets")
        contract_addr = self.env.current_contract_address()
        total_value = U128(0)

        for i in range(len(assets)):
            asset = assets.get(i)
            # Fetch balance of asset token held by this contract
            # Standard ERC-20: balance_of(address) -> U128
            balance = self.env.call(asset, "balance_of", contract_addr)
            price = self._get_oracle_price(asset)
            
            # Value = balance * price / scale
            # We assume price is scaled by 10^7. Let's adjust scale.
            total_value += (balance * price) / U128(10_000_000)

        # NAV = total_value * scale / supply
        # Let's scale by 10^6
        return (total_value * U128(1_000_000)) / supply

    def _accrue_fees(self):
        """Accrue manager fee (e.g. 2% annual fee) by minting new index tokens to the admin."""
        now = self._get_now()
        last_fee_time = self.storage.get("last_fee_timestamp", U64(0))
        if now <= last_fee_time:
            return

        elapsed = now - last_fee_time
        fee_bps = self.storage.get("manager_fee_bps", U64(0))
        supply = self.storage.get("total_supply", U128(0))

        if supply == U128(0) or fee_bps == U64(0):
            self.storage.set("last_fee_timestamp", now)
            return

        # Fee tokens to mint = supply * fee_bps * elapsed / (10000 * 31,536,000)
        fee_tokens = (supply * U128(fee_bps) * U128(elapsed)) / U128(315_360_000_000)

        if fee_tokens > U128(0):
            admin = self.storage.get("admin")
            # Mint fee tokens to admin
            self.storage.set("total_supply", supply + fee_tokens)
            admin_bal = self.storage.get(f"balance_{admin}", U128(0))
            self.storage.set(f"balance_{admin}", admin_bal + fee_tokens)

        self.storage.set("last_fee_timestamp", now)
