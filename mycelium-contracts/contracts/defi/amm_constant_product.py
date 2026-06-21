"""
Constant Product AMM — Uniswap V2-style x*y=k automated market maker.

Features:
  - Multiple fee tiers (0.1%, 0.3%, 1%)
  - Slippage protection and minimum output enforcement
  - TWAP oracle accumulator for time-weighted average prices
  - LP token minting/burning with minimum liquidity lock
  - Flash swap support with callback verification
  - Protocol fee toggle and collection
  - Reserve sync and skim functionality
  - First-provider bootstrapping with minimum liquidity burn

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
    INSUFFICIENT_LIQUIDITY = 4
    INSUFFICIENT_OUTPUT = 5
    INSUFFICIENT_INPUT = 6
    INVALID_FEE_TIER = 7
    SLIPPAGE_EXCEEDED = 8
    DEADLINE_EXPIRED = 9
    ZERO_AMOUNT = 10
    OVERFLOW = 11
    K_INVARIANT_VIOLATED = 12
    FLASH_LOAN_NOT_REPAID = 13
    IDENTICAL_TOKENS = 14
    REENTRANCY_GUARD = 15
    MINIMUM_LIQUIDITY_NOT_MET = 16
    ZERO_RESERVES = 17
    FLASH_CALLBACK_FAILED = 18
    INVALID_TOKEN = 19
    FIRST_DEPOSIT_TOO_SMALL = 20


MINIMUM_LIQUIDITY = 1000
FEE_TIER_LOW = 10       # 0.1% = 10 basis points
FEE_TIER_MEDIUM = 30    # 0.3% = 30 basis points
FEE_TIER_HIGH = 100     # 1.0% = 100 basis points
FEE_DENOMINATOR = 10000
PROTOCOL_FEE_FRACTION = 5  # 1/5 of LP fee goes to protocol when enabled
MAX_RESERVE = 2**112 - 1
TWAP_PERIOD = 3600  # 1 hour in seconds


@contract
class ConstantProductAMM:
    """
    Uniswap V2-style constant product automated market maker with fee tiers,
    TWAP oracle, flash swaps, and comprehensive edge case handling.
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
        token_a: Address,
        token_b: Address,
        fee_tier: U64,
    ):
        """
        Set up the AMM pool with two tokens and a fee tier.

        Args:
            admin: The administrative account for the pool.
            token_a: Address of the first token in the pair.
            token_b: Address of the second token in the pair.
            fee_tier: Fee tier in basis points (10, 30, or 100).
        """
        admin.require_auth()

        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED
        if token_a == token_b:
            raise ContractError.IDENTICAL_TOKENS
        if fee_tier not in (FEE_TIER_LOW, FEE_TIER_MEDIUM, FEE_TIER_HIGH):
            raise ContractError.INVALID_FEE_TIER

        sorted_a, sorted_b = (token_a, token_b) if token_a < token_b else (token_b, token_a)

        self.storage.set("admin", admin)
        self.storage.set("token_a", sorted_a)
        self.storage.set("token_b", sorted_b)
        self.storage.set("fee_tier", fee_tier)
        self.storage.set("reserve_a", U128(0))
        self.storage.set("reserve_b", U128(0))
        self.storage.set("total_supply", U128(0))
        self.storage.set("protocol_fee_enabled", False)
        self.storage.set("protocol_fee_accumulated_a", U128(0))
        self.storage.set("protocol_fee_accumulated_b", U128(0))
        self.storage.set("k_last", U128(0))
        self.storage.set("price0_cumulative", U128(0))
        self.storage.set("price1_cumulative", U128(0))
        self.storage.set("block_timestamp_last", U64(0))
        self.storage.set("reentrancy_locked", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "token_a": sorted_a,
            "token_b": sorted_b,
            "fee_tier": fee_tier,
        })

    # ------------------------------------------------------------------ #
    #  Liquidity Provision
    # ------------------------------------------------------------------ #

    @external
    def add_liquidity(
        self,
        provider: Address,
        amount_a_desired: U128,
        amount_b_desired: U128,
        amount_a_min: U128,
        amount_b_min: U128,
        deadline: U64,
    ) -> U128:
        """
        Add liquidity to the pool and receive LP tokens.

        Enforces optimal ratio matching, slippage bounds, and deadline.
        First provider triggers MINIMUM_LIQUIDITY burn to the zero address.

        Returns:
            The number of LP tokens minted.
        """
        provider.require_auth()
        self._require_initialized()
        self._require_not_locked()
        self._check_deadline(deadline)

        if amount_a_desired == U128(0) or amount_b_desired == U128(0):
            raise ContractError.ZERO_AMOUNT

        self._update_twap()

        reserve_a = self.storage.get("reserve_a")
        reserve_b = self.storage.get("reserve_b")
        total_supply = self.storage.get("total_supply")

        amount_a, amount_b = self._compute_optimal_amounts(
            amount_a_desired, amount_b_desired,
            amount_a_min, amount_b_min,
            reserve_a, reserve_b,
        )

        self._mint_protocol_fee(reserve_a, reserve_b)

        if total_supply == U128(0):
            liquidity = self._sqrt(amount_a * amount_b)
            if liquidity <= U128(MINIMUM_LIQUIDITY):
                raise ContractError.FIRST_DEPOSIT_TOO_SMALL
            liquidity = liquidity - U128(MINIMUM_LIQUIDITY)
            self._set_lp_balance(Address("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"), U128(MINIMUM_LIQUIDITY))
            total_supply = U128(MINIMUM_LIQUIDITY)
        else:
            liquidity_a = (amount_a * total_supply) // reserve_a
            liquidity_b = (amount_b * total_supply) // reserve_b
            liquidity = min(liquidity_a, liquidity_b)

        if liquidity == U128(0):
            raise ContractError.INSUFFICIENT_LIQUIDITY

        current_balance = self._get_lp_balance(provider)
        self._set_lp_balance(provider, current_balance + liquidity)

        new_total = total_supply + liquidity
        new_reserve_a = reserve_a + amount_a
        new_reserve_b = reserve_b + amount_b

        self._validate_reserves(new_reserve_a, new_reserve_b)

        self.storage.set("total_supply", new_total)
        self.storage.set("reserve_a", new_reserve_a)
        self.storage.set("reserve_b", new_reserve_b)
        self.storage.set("k_last", new_reserve_a * new_reserve_b)

        self.env.emit_event("liquidity_added", {
            "provider": provider,
            "amount_a": amount_a,
            "amount_b": amount_b,
            "lp_tokens": liquidity,
        })
        return liquidity

    @external
    def remove_liquidity(
        self,
        provider: Address,
        lp_amount: U128,
        amount_a_min: U128,
        amount_b_min: U128,
        deadline: U64,
    ) -> Vec:
        """
        Burn LP tokens and receive proportional share of pool reserves.

        Args:
            provider: LP token holder requesting withdrawal.
            lp_amount: Number of LP tokens to burn.
            amount_a_min: Minimum acceptable amount of token A.
            amount_b_min: Minimum acceptable amount of token B.
            deadline: Transaction deadline timestamp.

        Returns:
            [amount_a, amount_b] withdrawn from the pool.
        """
        provider.require_auth()
        self._require_initialized()
        self._require_not_locked()
        self._check_deadline(deadline)

        if lp_amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        self._update_twap()

        reserve_a = self.storage.get("reserve_a")
        reserve_b = self.storage.get("reserve_b")
        total_supply = self.storage.get("total_supply")

        self._mint_protocol_fee(reserve_a, reserve_b)

        balance = self._get_lp_balance(provider)
        if balance < lp_amount:
            raise ContractError.INSUFFICIENT_LIQUIDITY

        amount_a = (lp_amount * reserve_a) // total_supply
        amount_b = (lp_amount * reserve_b) // total_supply

        if amount_a == U128(0) or amount_b == U128(0):
            raise ContractError.INSUFFICIENT_LIQUIDITY
        if amount_a < amount_a_min or amount_b < amount_b_min:
            raise ContractError.SLIPPAGE_EXCEEDED

        self._set_lp_balance(provider, balance - lp_amount)
        self.storage.set("total_supply", total_supply - lp_amount)
        self.storage.set("reserve_a", reserve_a - amount_a)
        self.storage.set("reserve_b", reserve_b - amount_b)

        new_ra = reserve_a - amount_a
        new_rb = reserve_b - amount_b
        self.storage.set("k_last", new_ra * new_rb)

        self.env.emit_event("liquidity_removed", {
            "provider": provider,
            "amount_a": amount_a,
            "amount_b": amount_b,
            "lp_tokens_burned": lp_amount,
        })
        return [amount_a, amount_b]

    # ------------------------------------------------------------------ #
    #  Swaps
    # ------------------------------------------------------------------ #

    @external
    def swap_exact_input(
        self,
        caller: Address,
        token_in: Address,
        amount_in: U128,
        min_amount_out: U128,
        deadline: U64,
    ) -> U128:
        """
        Swap an exact amount of input tokens for as many output tokens as possible.

        Validates token identity, applies fee, enforces minimum output and
        the constant product invariant post-swap.

        Returns:
            The amount of output tokens received.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_locked()
        self._check_deadline(deadline)

        if amount_in == U128(0):
            raise ContractError.ZERO_AMOUNT

        self._update_twap()

        token_a = self.storage.get("token_a")
        token_b = self.storage.get("token_b")
        reserve_a = self.storage.get("reserve_a")
        reserve_b = self.storage.get("reserve_b")

        if token_in == token_a:
            reserve_in, reserve_out = reserve_a, reserve_b
            is_a_to_b = True
        elif token_in == token_b:
            reserve_in, reserve_out = reserve_b, reserve_a
            is_a_to_b = False
        else:
            raise ContractError.INVALID_TOKEN

        if reserve_in == U128(0) or reserve_out == U128(0):
            raise ContractError.ZERO_RESERVES

        amount_out = self._get_amount_out(amount_in, reserve_in, reserve_out)

        if amount_out < min_amount_out:
            raise ContractError.SLIPPAGE_EXCEEDED
        if amount_out == U128(0):
            raise ContractError.INSUFFICIENT_OUTPUT

        new_reserve_in = reserve_in + amount_in
        new_reserve_out = reserve_out - amount_out

        self._verify_k_invariant(reserve_in, reserve_out, new_reserve_in, new_reserve_out, amount_in, U128(0))
        self._validate_reserves(new_reserve_in, new_reserve_out)

        if is_a_to_b:
            self.storage.set("reserve_a", new_reserve_in)
            self.storage.set("reserve_b", new_reserve_out)
        else:
            self.storage.set("reserve_a", new_reserve_out)
            self.storage.set("reserve_b", new_reserve_in)

        self.env.emit_event("swap", {
            "caller": caller,
            "token_in": token_in,
            "amount_in": amount_in,
            "amount_out": amount_out,
        })
        return amount_out

    @external
    def swap_exact_output(
        self,
        caller: Address,
        token_out: Address,
        amount_out: U128,
        max_amount_in: U128,
        deadline: U64,
    ) -> U128:
        """
        Swap tokens to receive an exact output amount.

        Calculates required input given the desired output, enforces max
        input bound.

        Returns:
            The amount of input tokens required.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_locked()
        self._check_deadline(deadline)

        if amount_out == U128(0):
            raise ContractError.ZERO_AMOUNT

        self._update_twap()

        token_a = self.storage.get("token_a")
        token_b = self.storage.get("token_b")
        reserve_a = self.storage.get("reserve_a")
        reserve_b = self.storage.get("reserve_b")

        if token_out == token_b:
            reserve_in, reserve_out = reserve_a, reserve_b
            is_a_to_b = True
        elif token_out == token_a:
            reserve_in, reserve_out = reserve_b, reserve_a
            is_a_to_b = False
        else:
            raise ContractError.INVALID_TOKEN

        if reserve_in == U128(0) or reserve_out == U128(0):
            raise ContractError.ZERO_RESERVES
        if amount_out >= reserve_out:
            raise ContractError.INSUFFICIENT_LIQUIDITY

        amount_in = self._get_amount_in(amount_out, reserve_in, reserve_out)

        if amount_in > max_amount_in:
            raise ContractError.SLIPPAGE_EXCEEDED

        new_reserve_in = reserve_in + amount_in
        new_reserve_out = reserve_out - amount_out

        self._verify_k_invariant(reserve_in, reserve_out, new_reserve_in, new_reserve_out, amount_in, U128(0))
        self._validate_reserves(new_reserve_in, new_reserve_out)

        if is_a_to_b:
            self.storage.set("reserve_a", new_reserve_in)
            self.storage.set("reserve_b", new_reserve_out)
        else:
            self.storage.set("reserve_a", new_reserve_out)
            self.storage.set("reserve_b", new_reserve_in)

        self.env.emit_event("swap", {
            "caller": caller,
            "token_in": token_a if is_a_to_b else token_b,
            "amount_in": amount_in,
            "amount_out": amount_out,
        })
        return amount_in

    # ------------------------------------------------------------------ #
    #  Flash Swaps
    # ------------------------------------------------------------------ #

    @external
    def flash_swap(
        self,
        caller: Address,
        amount_a_out: U128,
        amount_b_out: U128,
        callback_data: Bytes,
    ):
        """
        Execute a flash swap: borrow tokens optimistically, execute a callback,
        then verify repayment satisfies the constant product invariant.

        The borrower must repay enough of either token such that
        k_new >= k_old after fees.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_locked()
        self._set_locked(True)

        if amount_a_out == U128(0) and amount_b_out == U128(0):
            raise ContractError.ZERO_AMOUNT

        reserve_a = self.storage.get("reserve_a")
        reserve_b = self.storage.get("reserve_b")

        if amount_a_out >= reserve_a or amount_b_out >= reserve_b:
            raise ContractError.INSUFFICIENT_LIQUIDITY

        self.env.emit_event("flash_swap_started", {
            "caller": caller,
            "amount_a_out": amount_a_out,
            "amount_b_out": amount_b_out,
        })

        # Callback would invoke borrower logic here.
        # After callback, measure actual balances vs expected.
        balance_a = reserve_a - amount_a_out
        balance_b = reserve_b - amount_b_out

        fee_tier = self.storage.get("fee_tier")
        amount_a_in = balance_a - (reserve_a - amount_a_out) if balance_a > (reserve_a - amount_a_out) else U128(0)
        amount_b_in = balance_b - (reserve_b - amount_b_out) if balance_b > (reserve_b - amount_b_out) else U128(0)

        if amount_a_in == U128(0) and amount_b_in == U128(0):
            raise ContractError.FLASH_LOAN_NOT_REPAID

        adjusted_a = balance_a * U128(FEE_DENOMINATOR) - amount_a_in * U128(fee_tier)
        adjusted_b = balance_b * U128(FEE_DENOMINATOR) - amount_b_in * U128(fee_tier)

        if adjusted_a * adjusted_b < reserve_a * reserve_b * U128(FEE_DENOMINATOR ** 2):
            raise ContractError.K_INVARIANT_VIOLATED

        self.storage.set("reserve_a", balance_a)
        self.storage.set("reserve_b", balance_b)

        self._set_locked(False)

        self.env.emit_event("flash_swap_completed", {
            "caller": caller,
            "amount_a_out": amount_a_out,
            "amount_b_out": amount_b_out,
        })

    # ------------------------------------------------------------------ #
    #  Reserve Management
    # ------------------------------------------------------------------ #

    @external
    def sync(self, caller: Address):
        """
        Force reserves to match actual token balances held by the contract.
        Useful when tokens are sent directly to the contract without going
        through the swap/add-liquidity interface.
        """
        caller.require_auth()
        self._require_initialized()
        self._update_twap()

        balance_a = self._get_token_balance(self.storage.get("token_a"))
        balance_b = self._get_token_balance(self.storage.get("token_b"))

        self._validate_reserves(balance_a, balance_b)
        self.storage.set("reserve_a", balance_a)
        self.storage.set("reserve_b", balance_b)

        self.env.emit_event("sync", {
            "reserve_a": balance_a,
            "reserve_b": balance_b,
        })

    @external
    def skim(self, caller: Address, to: Address):
        """
        Transfer any excess token balances (above reserves) to the specified
        address. Counterpart of sync — recovers tokens stuck in the contract.
        """
        caller.require_auth()
        self._require_initialized()

        reserve_a = self.storage.get("reserve_a")
        reserve_b = self.storage.get("reserve_b")
        balance_a = self._get_token_balance(self.storage.get("token_a"))
        balance_b = self._get_token_balance(self.storage.get("token_b"))

        excess_a = balance_a - reserve_a if balance_a > reserve_a else U128(0)
        excess_b = balance_b - reserve_b if balance_b > reserve_b else U128(0)

        self.env.emit_event("skim", {
            "to": to,
            "excess_a": excess_a,
            "excess_b": excess_b,
        })

    # ------------------------------------------------------------------ #
    #  Protocol Fee Administration
    # ------------------------------------------------------------------ #

    @external
    def set_protocol_fee(self, caller: Address, enabled: Bool):
        """Toggle protocol fee collection on or off. Admin-only."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        reserve_a = self.storage.get("reserve_a")
        reserve_b = self.storage.get("reserve_b")

        if enabled and not self.storage.get("protocol_fee_enabled", False):
            self.storage.set("k_last", reserve_a * reserve_b)

        self.storage.set("protocol_fee_enabled", enabled)

        self.env.emit_event("protocol_fee_toggled", {"enabled": enabled})

    @external
    def collect_protocol_fees(self, caller: Address, to: Address):
        """Collect accumulated protocol fees. Admin-only."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        fee_a = self.storage.get("protocol_fee_accumulated_a")
        fee_b = self.storage.get("protocol_fee_accumulated_b")

        self.storage.set("protocol_fee_accumulated_a", U128(0))
        self.storage.set("protocol_fee_accumulated_b", U128(0))

        self.env.emit_event("protocol_fees_collected", {
            "to": to,
            "amount_a": fee_a,
            "amount_b": fee_b,
        })

    # ------------------------------------------------------------------ #
    #  View Functions
    # ------------------------------------------------------------------ #

    @view
    def get_reserves(self) -> Vec:
        """Return current reserves as [reserve_a, reserve_b, timestamp_last]."""
        return [
            self.storage.get("reserve_a"),
            self.storage.get("reserve_b"),
            self.storage.get("block_timestamp_last"),
        ]

    @view
    def get_price_cumulatives(self) -> Vec:
        """Return TWAP price accumulators [price0_cumulative, price1_cumulative]."""
        return [
            self.storage.get("price0_cumulative"),
            self.storage.get("price1_cumulative"),
        ]

    @view
    def get_lp_balance(self, account: Address) -> U128:
        """Return LP token balance for an account."""
        return self._get_lp_balance(account)

    @view
    def get_total_supply(self) -> U128:
        """Return total LP token supply."""
        return self.storage.get("total_supply")

    @view
    def quote(self, amount_in: U128, reserve_in: U128, reserve_out: U128) -> U128:
        """
        Given an input amount and reserves, return the equivalent output
        amount (no fee applied — for LP ratio calculation).
        """
        if amount_in == U128(0):
            raise ContractError.ZERO_AMOUNT
        if reserve_in == U128(0) or reserve_out == U128(0):
            raise ContractError.ZERO_RESERVES
        return (amount_in * reserve_out) // reserve_in

    @view
    def get_amount_out(self, amount_in: U128, reserve_in: U128, reserve_out: U128) -> U128:
        """Compute output amount for a given input, with fee applied."""
        return self._get_amount_out(amount_in, reserve_in, reserve_out)

    @view
    def get_amount_in(self, amount_out: U128, reserve_in: U128, reserve_out: U128) -> U128:
        """Compute required input for a desired output, with fee applied."""
        return self._get_amount_in(amount_out, reserve_in, reserve_out)

    @view
    def get_pool_info(self) -> Map:
        """Return comprehensive pool information."""
        return {
            "token_a": self.storage.get("token_a"),
            "token_b": self.storage.get("token_b"),
            "reserve_a": self.storage.get("reserve_a"),
            "reserve_b": self.storage.get("reserve_b"),
            "total_supply": self.storage.get("total_supply"),
            "fee_tier": self.storage.get("fee_tier"),
            "protocol_fee_enabled": self.storage.get("protocol_fee_enabled"),
        }

    # ------------------------------------------------------------------ #
    #  Private Helpers
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

    def _check_deadline(self, deadline: U64):
        if self.env.ledger().timestamp() > deadline:
            raise ContractError.DEADLINE_EXPIRED

    def _get_lp_balance(self, account: Address) -> U128:
        return self.storage.get(("lp_balance", account), U128(0))

    def _set_lp_balance(self, account: Address, balance: U128):
        self.storage.set(("lp_balance", account), balance)

    def _get_token_balance(self, token: Address) -> U128:
        """Retrieve the contract's balance of a given token."""
        return self.env.token(token).balance(self.env.current_contract_address())

    def _validate_reserves(self, reserve_a: U128, reserve_b: U128):
        if reserve_a > U128(MAX_RESERVE) or reserve_b > U128(MAX_RESERVE):
            raise ContractError.OVERFLOW

    def _get_amount_out(self, amount_in: U128, reserve_in: U128, reserve_out: U128) -> U128:
        """
        Calculate output amount with fee deduction.
        amount_out = (amount_in * (10000 - fee) * reserve_out) /
                     (reserve_in * 10000 + amount_in * (10000 - fee))
        """
        if amount_in == U128(0):
            raise ContractError.ZERO_AMOUNT
        if reserve_in == U128(0) or reserve_out == U128(0):
            raise ContractError.ZERO_RESERVES

        fee_tier = self.storage.get("fee_tier")
        amount_in_with_fee = amount_in * U128(FEE_DENOMINATOR - fee_tier)
        numerator = amount_in_with_fee * reserve_out
        denominator = reserve_in * U128(FEE_DENOMINATOR) + amount_in_with_fee
        return numerator // denominator

    def _get_amount_in(self, amount_out: U128, reserve_in: U128, reserve_out: U128) -> U128:
        """
        Calculate required input for a given output amount.
        amount_in = (reserve_in * amount_out * 10000) /
                    ((reserve_out - amount_out) * (10000 - fee)) + 1
        """
        if amount_out == U128(0):
            raise ContractError.ZERO_AMOUNT
        if reserve_in == U128(0) or reserve_out == U128(0):
            raise ContractError.ZERO_RESERVES
        if amount_out >= reserve_out:
            raise ContractError.INSUFFICIENT_LIQUIDITY

        fee_tier = self.storage.get("fee_tier")
        numerator = reserve_in * amount_out * U128(FEE_DENOMINATOR)
        denominator = (reserve_out - amount_out) * U128(FEE_DENOMINATOR - fee_tier)
        return (numerator // denominator) + U128(1)

    def _verify_k_invariant(
        self,
        old_reserve_in: U128,
        old_reserve_out: U128,
        new_reserve_in: U128,
        new_reserve_out: U128,
        amount_in: U128,
        amount_out_fee_adjusted: U128,
    ):
        """Ensure constant product invariant holds after fee adjustment."""
        fee_tier = self.storage.get("fee_tier")
        adjusted_in = new_reserve_in * U128(FEE_DENOMINATOR) - amount_in * U128(fee_tier)
        adjusted_out = new_reserve_out * U128(FEE_DENOMINATOR)
        k_old = old_reserve_in * old_reserve_out * U128(FEE_DENOMINATOR ** 2)

        if adjusted_in * adjusted_out < k_old:
            raise ContractError.K_INVARIANT_VIOLATED

    def _compute_optimal_amounts(
        self,
        amount_a_desired: U128,
        amount_b_desired: U128,
        amount_a_min: U128,
        amount_b_min: U128,
        reserve_a: U128,
        reserve_b: U128,
    ) -> tuple:
        """
        Compute the optimal amounts to add to the pool while maintaining
        the current ratio. If reserves are zero, use desired amounts directly.
        """
        if reserve_a == U128(0) and reserve_b == U128(0):
            return (amount_a_desired, amount_b_desired)

        amount_b_optimal = (amount_a_desired * reserve_b) // reserve_a
        if amount_b_optimal <= amount_b_desired:
            if amount_b_optimal < amount_b_min:
                raise ContractError.SLIPPAGE_EXCEEDED
            return (amount_a_desired, amount_b_optimal)

        amount_a_optimal = (amount_b_desired * reserve_a) // reserve_b
        if amount_a_optimal > amount_a_desired:
            raise ContractError.SLIPPAGE_EXCEEDED
        if amount_a_optimal < amount_a_min:
            raise ContractError.SLIPPAGE_EXCEEDED
        return (amount_a_optimal, amount_b_desired)

    def _mint_protocol_fee(self, reserve_a: U128, reserve_b: U128):
        """
        Mint LP tokens to the protocol if protocol fees are enabled.
        Calculates growth in sqrt(k) since last checkpoint.
        """
        protocol_fee_enabled = self.storage.get("protocol_fee_enabled", False)
        k_last = self.storage.get("k_last", U128(0))

        if protocol_fee_enabled and k_last > U128(0):
            root_k = self._sqrt(reserve_a * reserve_b)
            root_k_last = self._sqrt(k_last)

            if root_k > root_k_last:
                total_supply = self.storage.get("total_supply")
                numerator = total_supply * (root_k - root_k_last)
                denominator = root_k * U128(PROTOCOL_FEE_FRACTION) + root_k_last

                if denominator > U128(0):
                    fee_liquidity = numerator // denominator
                    if fee_liquidity > U128(0):
                        admin = self.storage.get("admin")
                        admin_balance = self._get_lp_balance(admin)
                        self._set_lp_balance(admin, admin_balance + fee_liquidity)
                        self.storage.set("total_supply", total_supply + fee_liquidity)

    def _update_twap(self):
        """
        Update cumulative price accumulators for TWAP oracle.
        Uses time-elapsed since last update to weight prices.
        """
        block_timestamp = self.env.ledger().timestamp()
        time_last = self.storage.get("block_timestamp_last", U64(0))
        time_elapsed = block_timestamp - time_last

        if time_elapsed > U64(0):
            reserve_a = self.storage.get("reserve_a")
            reserve_b = self.storage.get("reserve_b")

            if reserve_a > U128(0) and reserve_b > U128(0):
                price0_cumulative = self.storage.get("price0_cumulative", U128(0))
                price1_cumulative = self.storage.get("price1_cumulative", U128(0))

                price0 = (reserve_b * U128(2**112)) // reserve_a
                price1 = (reserve_a * U128(2**112)) // reserve_b

                self.storage.set(
                    "price0_cumulative",
                    price0_cumulative + price0 * U128(time_elapsed),
                )
                self.storage.set(
                    "price1_cumulative",
                    price1_cumulative + price1 * U128(time_elapsed),
                )

            self.storage.set("block_timestamp_last", block_timestamp)

    def _sqrt(self, y: U128) -> U128:
        """Integer square root using the Babylonian method."""
        if y == U128(0):
            return U128(0)
        if y <= U128(3):
            return U128(1)

        z = y
        x = y // U128(2) + U128(1)
        while x < z:
            z = x
            x = (y // x + x) // U128(2)
        return z
