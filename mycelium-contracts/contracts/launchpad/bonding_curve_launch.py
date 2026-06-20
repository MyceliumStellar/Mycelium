"""
Bonding Curve Launch — Continuous issuance pricing curve, burn/mint logic, reserve ratio updates, liquidity pool, anti-frontrunning protection.

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
    PAUSED = 4
    SLIPPAGE_EXCEEDED = 5
    INSUFFICIENT_RESERVE = 6
    INVALID_RATIO = 7
    INVALID_SLOPE = 8
    INVALID_BASE_PRICE = 9
    MAX_SUPPLY_EXCEEDED = 10
    MIN_PURCHASE_NOT_MET = 11
    ZERO_AMOUNT = 12

@contract
class BondingCurveLaunch:
    """Continuous token bonding curve launchpad contract with dynamic reserve ratio scaling."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        reserve_token: Address,
        continuous_token: Address,
        slope: U128,
        base_price: U128,
        reserve_ratio: U32,  # parts per million (1 to 1,000,000)
        mint_fee_bps: U32,   # basis points (e.g. 100 = 1%)
        burn_fee_bps: U32,
        treasury: Address,
        max_supply: U128,
    ):
        """Initialize the bonding curve launchpad.

        Args:
            admin: Administrative address.
            reserve_token: Token used to buy continuous tokens.
            continuous_token: Token being minted and sold.
            slope: Slope coefficient of the linear curve.
            base_price: Starting base price of the token.
            reserve_ratio: Parts per million (PPM) reserve ratio multiplier.
            mint_fee_bps: Fee in basis points taken during minting.
            burn_fee_bps: Fee in basis points taken during burning.
            treasury: Address receiving transaction fees.
            max_supply: Maximum allowable supply for the continuous token.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if reserve_ratio == 0 or reserve_ratio > 1000000:
            raise ContractError.INVALID_RATIO

        if slope == 0:
            raise ContractError.INVALID_SLOPE

        if base_price == 0:
            raise ContractError.INVALID_BASE_PRICE

        self.storage.set("admin", admin)
        self.storage.set("reserve_token", reserve_token)
        self.storage.set("continuous_token", continuous_token)
        self.storage.set("slope", slope)
        self.storage.set("base_price", base_price)
        self.storage.set("reserve_ratio", reserve_ratio)
        self.storage.set("mint_fee_bps", mint_fee_bps)
        self.storage.set("burn_fee_bps", burn_fee_bps)
        self.storage.set("treasury", treasury)
        self.storage.set("max_supply", max_supply)
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "reserve_token": reserve_token,
            "continuous_token": continuous_token,
            "slope": slope,
            "base_price": base_price,
            "reserve_ratio": reserve_ratio,
        })

    @external
    def buy_exact_tokens(
        self,
        caller: Address,
        tokens_to_mint: U128,
        max_reserve_in: U128,
    ) -> U128:
        """Buy an exact amount of continuous tokens, paying reserve tokens up to max_reserve_in.

        Args:
            caller: Account purchasing and receiving the tokens.
            tokens_to_mint: Amount of continuous tokens to mint.
            max_reserve_in: Maximum reserve tokens allowed to be spent (anti-frontrunning).
        """
        self._require_initialized()
        self._require_not_paused()
        caller.require_auth()

        if tokens_to_mint == 0:
            raise ContractError.ZERO_AMOUNT

        continuous_token = self.storage.get("continuous_token")
        current_supply = self._get_token_supply(continuous_token)

        max_supply = self.storage.get("max_supply")
        if current_supply + tokens_to_mint > max_supply:
            raise ContractError.MAX_SUPPLY_EXCEEDED

        # Calculate exact reserve cost based on current supply
        reserve_cost = self._calculate_mint_reserve(current_supply, tokens_to_mint)
        
        # Calculate fees
        mint_fee_bps = self.storage.get("mint_fee_bps")
        fee_amount = (reserve_cost * U128(mint_fee_bps)) / U128(10000)
        total_reserve_needed = reserve_cost + fee_amount

        if total_reserve_needed > max_reserve_in:
            raise ContractError.SLIPPAGE_EXCEEDED

        # Transfer reserve tokens from caller to contract/treasury
        reserve_token = self.storage.get("reserve_token")
        treasury = self.storage.get("treasury")
        
        if fee_amount > 0:
            self.env.invoke_contract(
                reserve_token,
                "transfer",
                [caller, treasury, fee_amount]
            )
        
        self.env.invoke_contract(
            reserve_token,
            "transfer",
            [caller, self.env.current_contract_address(), reserve_cost]
        )

        # Mint continuous tokens to the caller
        self.env.invoke_contract(
            continuous_token,
            "mint",
            [caller, tokens_to_mint]
        )

        self.env.emit_event("tokens_purchased", {
            "buyer": caller,
            "minted": tokens_to_mint,
            "reserve_spent": reserve_cost,
            "fee": fee_amount,
            "new_supply": current_supply + tokens_to_mint,
        })

        return total_reserve_needed

    @external
    def sell_exact_tokens(
        self,
        caller: Address,
        tokens_to_burn: U128,
        min_reserve_out: U128,
    ) -> U128:
        """Sell an exact amount of continuous tokens, receiving reserve tokens of at least min_reserve_out.

        Args:
            caller: Account selling and burning the tokens.
            tokens_to_burn: Amount of continuous tokens to burn.
            min_reserve_out: Minimum reserve tokens allowed to be received (anti-frontrunning).
        """
        self._require_initialized()
        self._require_not_paused()
        caller.require_auth()

        if tokens_to_burn == 0:
            raise ContractError.ZERO_AMOUNT

        continuous_token = self.storage.get("continuous_token")
        current_supply = self._get_token_supply(continuous_token)

        if tokens_to_burn > current_supply:
            raise ContractError.ZERO_AMOUNT

        # Calculate reserve returned
        reserve_returned = self._calculate_burn_reserve(current_supply, tokens_to_burn)

        # Calculate fees
        burn_fee_bps = self.storage.get("burn_fee_bps")
        fee_amount = (reserve_returned * U128(burn_fee_bps)) / U128(10000)
        net_reserve_out = reserve_returned - fee_amount

        if net_reserve_out < min_reserve_out:
            raise ContractError.SLIPPAGE_EXCEEDED

        # Check reserve balance
        reserve_token = self.storage.get("reserve_token")
        contract_balance = self.env.invoke_contract(
            reserve_token,
            "balance",
            [self.env.current_contract_address()]
        )
        if contract_balance < reserve_returned:
            raise ContractError.INSUFFICIENT_RESERVE

        # Burn continuous tokens from caller
        self.env.invoke_contract(
            continuous_token,
            "burn",
            [caller, tokens_to_burn]
        )

        # Transfer reserve tokens to caller and treasury
        treasury = self.storage.get("treasury")
        if fee_amount > 0:
            self.env.invoke_contract(
                reserve_token,
                "transfer",
                [self.env.current_contract_address(), treasury, fee_amount]
            )

        self.env.invoke_contract(
            reserve_token,
            "transfer",
            [self.env.current_contract_address(), caller, net_reserve_out]
        )

        self.env.emit_event("tokens_sold", {
            "seller": caller,
            "burned": tokens_to_burn,
            "reserve_received": net_reserve_out,
            "fee": fee_amount,
            "new_supply": current_supply - tokens_to_burn,
        })

        return net_reserve_out

    @external
    def update_reserve_ratio(self, admin: Address, new_ratio: U32):
        """Update the reserve ratio parameter of the curve.

        Args:
            admin: Administrative address.
            new_ratio: New reserve ratio in PPM.
        """
        self._require_initialized()
        admin.require_auth()
        self._require_admin(admin)

        if new_ratio == 0 or new_ratio > 1000000:
            raise ContractError.INVALID_RATIO

        old_ratio = self.storage.get("reserve_ratio")
        self.storage.set("reserve_ratio", new_ratio)

        self.env.emit_event("reserve_ratio_updated", {
            "old_ratio": old_ratio,
            "new_ratio": new_ratio,
        })

    @external
    def update_curve_parameters(self, admin: Address, new_slope: U128, new_base_price: U128):
        """Update the pricing curve slope and base price.

        Args:
            admin: Administrative address.
            new_slope: New slope value.
            new_base_price: New base price value.
        """
        self._require_initialized()
        admin.require_auth()
        self._require_admin(admin)

        if new_slope == 0:
            raise ContractError.INVALID_SLOPE
        if new_base_price == 0:
            raise ContractError.INVALID_BASE_PRICE

        self.storage.set("slope", new_slope)
        self.storage.set("base_price", new_base_price)

        self.env.emit_event("curve_parameters_updated", {
            "new_slope": new_slope,
            "new_base_price": new_base_price,
        })

    @external
    def set_paused(self, admin: Address, paused: Bool):
        """Pause or unpause minting and burning.

        Args:
            admin: Administrative address.
            paused: Boolean representing desired paused state.
        """
        self._require_initialized()
        admin.require_auth()
        self._require_admin(admin)

        self.storage.set("paused", paused)
        self.env.emit_event("paused_state_changed", {"paused": paused})

    @view
    def get_price(self, supply: U128) -> U128:
        """Get the spot price at a given continuous token supply.

        Args:
            supply: Supply amount.
        """
        slope = self.storage.get("slope")
        base_price = self.storage.get("base_price")
        ratio = U128(self.storage.get("reserve_ratio"))

        # Base price formula: Price = (slope * supply + base_price)
        # Scaled by reserve ratio: Price_Scaled = Price * (ratio / 1,000,000)
        virtual_price = slope * supply + base_price
        scaled_price = (virtual_price * ratio) / U128(1000000)
        return scaled_price

    @view
    def calculate_mint_cost(self, tokens_to_mint: U128) -> Map:
        """Calculate reserve required and fees for minting a specific amount of tokens.

        Args:
            tokens_to_mint: Continuous tokens to mint.
        """
        continuous_token = self.storage.get("continuous_token")
        current_supply = self._get_token_supply(continuous_token)
        
        reserve_cost = self._calculate_mint_reserve(current_supply, tokens_to_mint)
        mint_fee_bps = self.storage.get("mint_fee_bps")
        fee_amount = (reserve_cost * U128(mint_fee_bps)) / U128(10000)
        
        res = Map()
        res.set("reserve_cost", reserve_cost)
        res.set("fee_amount", fee_amount)
        res.set("total_cost", reserve_cost + fee_amount)
        return res

    @view
    def calculate_burn_refund(self, tokens_to_burn: U128) -> Map:
        """Calculate reserve refund and fees for burning a specific amount of tokens.

        Args:
            tokens_to_burn: Continuous tokens to burn.
        """
        continuous_token = self.storage.get("continuous_token")
        current_supply = self._get_token_supply(continuous_token)
        
        reserve_refund = self._calculate_burn_reserve(current_supply, tokens_to_burn)
        burn_fee_bps = self.storage.get("burn_fee_bps")
        fee_amount = (reserve_refund * U128(burn_fee_bps)) / U128(10000)
        
        res = Map()
        res.set("reserve_refund", reserve_refund)
        res.set("fee_amount", fee_amount)
        res.set("net_refund", reserve_refund - fee_amount)
        return res

    @view
    def get_info(self) -> Map:
        """Retrieve bonding curve parameters and status info."""
        res = Map()
        res.set("admin", self.storage.get("admin"))
        res.set("reserve_token", self.storage.get("reserve_token"))
        res.set("continuous_token", self.storage.get("continuous_token"))
        res.set("slope", self.storage.get("slope"))
        res.set("base_price", self.storage.get("base_price"))
        res.set("reserve_ratio", self.storage.get("reserve_ratio"))
        res.set("paused", self.storage.get("paused"))
        res.set("max_supply", self.storage.get("max_supply"))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_not_paused(self):
        if self.storage.get("paused", False):
            raise ContractError.PAUSED

    def _get_token_supply(self, token: Address) -> U128:
        # Call custom token contract "total_supply"
        return self.env.invoke_contract(token, "total_supply", [])

    def _calculate_mint_reserve(self, current_supply: U128, dS: U128) -> U128:
        # Integrals of Linear function: (slope * S + base_price)
        # Cost = dS * (P_0 + slope * dS / 2)
        # where P_0 = slope * current_supply + base_price
        slope = self.storage.get("slope")
        base_price = self.storage.get("base_price")
        ratio = U128(self.storage.get("reserve_ratio"))

        p0 = slope * current_supply + base_price
        # term = p0 + (slope * dS) / 2
        # Use intermediate multiplication to avoid division precision loss
        term = p0 * U128(2) + slope * dS
        
        # Integral = dS * term / 2
        raw_reserve = (dS * term) / U128(2)
        
        # Apply reserve ratio
        scaled_reserve = (raw_reserve * ratio) / U128(1000000)
        return scaled_reserve

    def _calculate_burn_reserve(self, current_supply: U128, dS: U128) -> U128:
        # Integral from S_1 = current_supply - dS to S_0 = current_supply
        # Refund = dS * (P_0 - slope * dS / 2)
        # where P_0 = slope * current_supply + base_price
        slope = self.storage.get("slope")
        base_price = self.storage.get("base_price")
        ratio = U128(self.storage.get("reserve_ratio"))

        p0 = slope * current_supply + base_price
        
        # term = p0 * 2 - slope * dS
        term = p0 * U128(2) - slope * dS
        raw_reserve = (dS * term) / U128(2)
        
        scaled_reserve = (raw_reserve * ratio) / U128(1000000)
        return scaled_reserve
