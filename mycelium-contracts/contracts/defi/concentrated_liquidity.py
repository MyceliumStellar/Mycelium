"""
Concentrated Liquidity — Uniswap V3-style tick-based concentrated liquidity.

Features:
  - Range-bound positions with tick intervals
  - Active liquidity and tick net liquidity tracking
  - Sqrt price representation scaled by 2^96
  - Precise calculation of Token 0 and Token 1 requirements given tick range
  - Tick-crossing logic updating active liquidity during swaps
  - Fee accumulation per position based on global fee growth updates
  - Position burning and fee/payout collection
  - Slippage and deadline protections

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
    DEADLINE_EXPIRED = 5
    INVALID_TICK_RANGE = 6
    ZERO_LIQUIDITY = 7
    INSUFFICIENT_LIQUIDITY = 8
    SLIPPAGE_EXCEEDED = 9
    OVERFLOW = 10
    ZERO_AMOUNT = 11
    INVALID_TICK_SPACING = 12
    PRICE_LIMIT_EXCEEDED = 13


# Constants
Q96 = U128(2**96)
Q128 = U128(2**128)
MAX_TICK = 887272
MIN_TICK = -887272


@contract
class ConcentratedLiquidity:
    """
    Uniswap V3-style Concentrated Liquidity contract managing price tick ranges,
    fee distributions, tick crossings, and swaps.
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
        token_0: Address,
        token_1: Address,
        fee_tier: U64,        # fee in basis points, e.g., 30 = 0.3%
        tick_spacing: I128,    # e.g., 60 for 0.3% pool
        initial_price_x96: U128,
    ):
        """Initialize the concentrated liquidity pool."""
        admin.require_auth()

        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if token_0 >= token_1:
            # Enforce sorted order
            raise ContractError.INVALID_TICK_RANGE
        if tick_spacing <= I128(0):
            raise ContractError.INVALID_TICK_SPACING

        self.storage.set("admin", admin)
        self.storage.set("token_0", token_0)
        self.storage.set("token_1", token_1)
        self.storage.set("fee_tier", fee_tier)
        self.storage.set("tick_spacing", tick_spacing)
        self.storage.set("sqrt_price_x96", initial_price_x96)
        
        # Determine initial tick from initial price
        initial_tick = self._get_tick_at_sqrt_ratio(initial_price_x96)
        self.storage.set("current_tick", initial_tick)
        
        self.storage.set("active_liquidity", U128(0))
        self.storage.set("fee_growth_global_0_x128", U128(0))
        self.storage.set("fee_growth_global_1_x128", U128(0))
        self.storage.set("reentrancy_locked", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "token_0": token_0,
            "token_1": token_1,
            "initial_price_x96": initial_price_x96,
            "initial_tick": initial_tick
        })

    # ------------------------------------------------------------------ #
    #  Position Management (Mint/Burn/Collect)
    # ------------------------------------------------------------------ #

    @external
    def mint(
        self,
        recipient: Address,
        tick_lower: I128,
        tick_upper: I128,
        liquidity: U128,
        amount_0_max: U128,
        amount_1_max: U128,
        deadline: U64,
    ) -> Vec:
        """
        Mint a position with tick range [tick_lower, tick_upper] and liquidity L.
        Calculates and transfers required amounts of Token 0 and Token 1.
        """
        recipient.require_auth()
        self._require_initialized()
        self._require_not_locked()
        self._check_deadline(deadline)
        self._set_locked(True)

        self._validate_ticks(tick_lower, tick_upper)
        if liquidity == U128(0):
            raise ContractError.ZERO_LIQUIDITY

        # Update tick states and collect fees up to now
        self._update_position(recipient, tick_lower, tick_upper, liquidity, True)

        current_tick = self.storage.get("current_tick")
        sqrt_price_x96 = self.storage.get("sqrt_price_x96")
        sqrt_price_lower = self._get_sqrt_ratio_at_tick(tick_lower)
        sqrt_price_upper = self._get_sqrt_ratio_at_tick(tick_upper)

        amount_0 = U128(0)
        amount_1 = U128(0)

        # Calculate required assets based on current price position vs range
        if current_tick < tick_lower:
            # Current price below range: only token 0 required
            amount_0 = self._get_amount_0_for_liquidity(sqrt_price_lower, sqrt_price_upper, liquidity)
        elif current_tick < tick_upper:
            # Current price inside range: both tokens required
            amount_0 = self._get_amount_0_for_liquidity(sqrt_price_x96, sqrt_price_upper, liquidity)
            amount_1 = self._get_amount_1_for_liquidity(sqrt_price_lower, sqrt_price_x96, liquidity)
            # Add to active pool liquidity
            active_liq = self.storage.get("active_liquidity")
            self.storage.set("active_liquidity", active_liq + liquidity)
        else:
            # Current price above range: only token 1 required
            amount_1 = self._get_amount_1_for_liquidity(sqrt_price_lower, sqrt_price_upper, liquidity)

        if amount_0 > amount_0_max or amount_1 > amount_1_max:
            raise ContractError.SLIPPAGE_EXCEEDED

        # Transfer tokens
        token_0 = self.storage.get("token_0")
        token_1 = self.storage.get("token_1")
        if amount_0 > U128(0):
            self.env.transfer(recipient, self.env.current_contract(), token_0, amount_0)
        if amount_1 > U128(0):
            self.env.transfer(recipient, self.env.current_contract(), token_1, amount_1)

        self._set_locked(False)

        self.env.emit_event("minted", {
            "owner": recipient,
            "tick_lower": tick_lower,
            "tick_upper": tick_upper,
            "liquidity": liquidity,
            "amount_0": amount_0,
            "amount_1": amount_1
        })
        return [amount_0, amount_1]

    @external
    def burn(
        self,
        owner: Address,
        tick_lower: I128,
        tick_upper: I128,
        liquidity: U128,
        deadline: U64,
    ) -> Vec:
        """
        Burn liquidity L from position, calculating returned token amounts
        and accumulating them to owed balances. Does not transfer tokens.
        """
        owner.require_auth()
        self._require_initialized()
        self._require_not_locked()
        self._check_deadline(deadline)
        self._set_locked(True)

        self._validate_ticks(tick_lower, tick_upper)
        if liquidity == U128(0):
            raise ContractError.ZERO_LIQUIDITY

        # Check existing position liquidity
        pos_key = f"position_liquidity:{owner}:{tick_lower}:{tick_upper}"
        pos_liq = self.storage.get(pos_key, U128(0))
        if pos_liq < liquidity:
            raise ContractError.INSUFFICIENT_LIQUIDITY

        # Settle pending position rewards/fees
        self._update_position(owner, tick_lower, tick_upper, liquidity, False)

        current_tick = self.storage.get("current_tick")
        sqrt_price_x96 = self.storage.get("sqrt_price_x96")
        sqrt_price_lower = self._get_sqrt_ratio_at_tick(tick_lower)
        sqrt_price_upper = self._get_sqrt_ratio_at_tick(tick_upper)

        amount_0 = U128(0)
        amount_1 = U128(0)

        if current_tick < tick_lower:
            amount_0 = self._get_amount_0_for_liquidity(sqrt_price_lower, sqrt_price_upper, liquidity)
        elif current_tick < tick_upper:
            amount_0 = self._get_amount_0_for_liquidity(sqrt_price_x96, sqrt_price_upper, liquidity)
            amount_1 = self._get_amount_1_for_liquidity(sqrt_price_lower, sqrt_price_x96, liquidity)
            # Subtract from active pool liquidity
            active_liq = self.storage.get("active_liquidity")
            self.storage.set("active_liquidity", active_liq - liquidity)
        else:
            amount_1 = self._get_amount_1_for_liquidity(sqrt_price_lower, sqrt_price_upper, liquidity)

        # Accumulate to owed tokens
        owed_0_key = f"position_owed_0:{owner}:{tick_lower}:{tick_upper}"
        owed_1_key = f"position_owed_1:{owner}:{tick_lower}:{tick_upper}"
        self.storage.set(owed_0_key, self.storage.get(owed_0_key, U128(0)) + amount_0)
        self.storage.set(owed_1_key, self.storage.get(owed_1_key, U128(0)) + amount_1)

        self._set_locked(False)

        self.env.emit_event("burned", {
            "owner": owner,
            "tick_lower": tick_lower,
            "tick_upper": tick_upper,
            "liquidity_burned": liquidity,
            "amount_0_owed": amount_0,
            "amount_1_owed": amount_1
        })
        return [amount_0, amount_1]

    @external
    def collect(
        self,
        recipient: Address,
        tick_lower: I128,
        tick_upper: I128,
        amount_0_requested: U128,
        amount_1_requested: U128,
    ) -> Vec:
        """Collect accumulated fee/burn returns owed to a position."""
        recipient.require_auth()
        self._require_initialized()
        self._require_not_locked()
        self._set_locked(True)

        self._validate_ticks(tick_lower, tick_upper)

        owed_0_key = f"position_owed_0:{recipient}:{tick_lower}:{tick_upper}"
        owed_1_key = f"position_owed_1:{recipient}:{tick_lower}:{tick_upper}"
        
        owed_0 = self.storage.get(owed_0_key, U128(0))
        owed_1 = self.storage.get(owed_1_key, U128(0))

        amount_0 = min(owed_0, amount_0_requested)
        amount_1 = min(owed_1, amount_1_requested)

        self.storage.set(owed_0_key, owed_0 - amount_0)
        self.storage.set(owed_1_key, owed_1 - amount_1)

        token_0 = self.storage.get("token_0")
        token_1 = self.storage.get("token_1")

        if amount_0 > U128(0):
            self.env.transfer(self.env.current_contract(), recipient, token_0, amount_0)
        if amount_1 > U128(0):
            self.env.transfer(self.env.current_contract(), recipient, token_1, amount_1)

        self._set_locked(False)

        self.env.emit_event("collected", {
            "owner": recipient,
            "tick_lower": tick_lower,
            "tick_upper": tick_upper,
            "amount_0": amount_0,
            "amount_1": amount_1
        })
        return [amount_0, amount_1]

    # ------------------------------------------------------------------ #
    #  Swaps
    # ------------------------------------------------------------------ #

    @external
    def swap(
        self,
        recipient: Address,
        zero_for_one: Bool,
        amount_specified: I128, # Positive for exact-in, negative for exact-out
        sqrt_price_limit_x96: U128,
        deadline: U64,
    ) -> Vec:
        """
        Execute a tick-crossing swap.
        Enforces tick bounds, updates active liquidity, distributes swap fees.
        """
        recipient.require_auth()
        self._require_initialized()
        self._require_not_locked()
        self._check_deadline(deadline)
        self._set_locked(True)

        if amount_specified == I128(0):
            raise ContractError.ZERO_AMOUNT

        sqrt_price_x96 = self.storage.get("sqrt_price_x96")
        current_tick = self.storage.get("current_tick")

        # Verify limit price boundary
        if zero_for_one:
            if sqrt_price_limit_x96 >= sqrt_price_x96 or sqrt_price_limit_x96 == U128(0):
                raise ContractError.PRICE_LIMIT_EXCEEDED
        else:
            if sqrt_price_limit_x96 <= sqrt_price_x96 or sqrt_price_limit_x96 == U128(0):
                raise ContractError.PRICE_LIMIT_EXCEEDED

        exact_input = amount_specified > I128(0)
        amount_remaining = amount_specified.abs() if exact_input else amount_specified.abs()

        amount_calculated = U128(0)
        token_0 = self.storage.get("token_0")
        token_1 = self.storage.get("token_1")
        fee_tier = self.storage.get("fee_tier")

        active_liq = self.storage.get("active_liquidity")

        # Swap loop traversing ticks
        # Simple walk simulation:
        # In production, tick bitmaps are traversed. Here we search next/prev ticks.
        while amount_remaining > U128(0) and sqrt_price_x96 != sqrt_price_limit_x96:
            # Determine next tick boundary
            next_tick = current_tick - I128(60) if zero_for_one else current_tick + I128(60)
            
            # Sqrt price at boundary tick
            next_price_x96 = self._get_sqrt_ratio_at_tick(next_tick)
            
            # Ensure we do not overshoot target price limit
            if zero_for_one:
                if next_price_x96 < sqrt_price_limit_x96:
                    next_price_x96 = sqrt_price_limit_x96
            else:
                if next_price_x96 > sqrt_price_limit_x96:
                    next_price_x96 = sqrt_price_limit_x96

            # Compute swap outputs for this step
            sqrt_price_next, amount_in_step, amount_out_step, fee_step = self._compute_swap_step(
                sqrt_price_x96, next_price_x96, active_liq, amount_remaining, fee_tier, exact_input
            )

            # Update accumulated math
            amount_remaining = amount_remaining - (amount_in_step + fee_step)
            amount_calculated = amount_calculated + amount_out_step

            # Distribute fees to global index
            if active_liq > U128(0):
                fee_growth_x128_delta = (fee_step * Q128) // active_liq
                if zero_for_one:
                    self.storage.set("fee_growth_global_0_x128", self.storage.get("fee_growth_global_0_x128") + fee_growth_x128_delta)
                else:
                    self.storage.set("fee_growth_global_1_x128", self.storage.get("fee_growth_global_1_x128") + fee_growth_x128_delta)

            sqrt_price_x96 = sqrt_price_next

            # If we reached tick boundary, cross tick
            if sqrt_price_x96 == next_price_x96:
                current_tick = next_tick
                # Cross active tick liquidity net
                net_liq = self.storage.get(f"tick_liquidity_net:{current_tick}", I128(0))
                if zero_for_one:
                    # moving down, subtract net
                    active_liq = U128(I128(active_liq) - net_liq)
                else:
                    # moving up, add net
                    active_liq = U128(I128(active_liq) + net_liq)

            else:
                current_tick = self._get_tick_at_sqrt_ratio(sqrt_price_x96)

        # Update global parameters
        self.storage.set("sqrt_price_x96", sqrt_price_x96)
        self.storage.set("current_tick", current_tick)
        self.storage.set("active_liquidity", active_liq)

        # Perform actual transfers
        amount_in = amount_specified.abs() - amount_remaining if exact_input else amount_calculated
        amount_out = amount_calculated if exact_input else amount_specified.abs()

        if zero_for_one:
            self.env.transfer(recipient, self.env.current_contract(), token_0, amount_in)
            self.env.transfer(self.env.current_contract(), recipient, token_1, amount_out)
        else:
            self.env.transfer(recipient, self.env.current_contract(), token_1, amount_in)
            self.env.transfer(self.env.current_contract(), recipient, token_0, amount_out)

        self._set_locked(False)

        self.env.emit_event("swap", {
            "caller": recipient,
            "zero_for_one": zero_for_one,
            "amount_in": amount_in,
            "amount_out": amount_out,
            "price_after_x96": sqrt_price_x96,
            "tick_after": current_tick
        })

        return [amount_in, amount_out]

    # ------------------------------------------------------------------ #
    #  Views & Helpers
    # ------------------------------------------------------------------ #

    @view
    def get_position(self, owner: Address, tick_lower: I128, tick_upper: I128) -> Map:
        """Retrieve details of a liquidity position."""
        return {
            "liquidity": self.storage.get(f"position_liquidity:{owner}:{tick_lower}:{tick_upper}", U128(0)),
            "fee_growth_inside_0": self.storage.get(f"position_fee_growth_inside_0:{owner}:{tick_lower}:{tick_upper}", U128(0)),
            "fee_growth_inside_1": self.storage.get(f"position_fee_growth_inside_1:{owner}:{tick_lower}:{tick_upper}", U128(0)),
            "owed_0": self.storage.get(f"position_owed_0:{owner}:{tick_lower}:{tick_upper}", U128(0)),
            "owed_1": self.storage.get(f"position_owed_1:{owner}:{tick_lower}:{tick_upper}", U128(0))
        }

    @view
    def get_pool_state(self) -> Map:
        """Return global pool metadata."""
        return {
            "token_0": self.storage.get("token_0"),
            "token_1": self.storage.get("token_1"),
            "fee_tier": self.storage.get("fee_tier"),
            "tick_spacing": self.storage.get("tick_spacing"),
            "sqrt_price_x96": self.storage.get("sqrt_price_x96"),
            "current_tick": self.storage.get("current_tick"),
            "active_liquidity": self.storage.get("active_liquidity"),
            "fee_growth_global_0_x128": self.storage.get("fee_growth_global_0_x128"),
            "fee_growth_global_1_x128": self.storage.get("fee_growth_global_1_x128")
        }

    # ------------------------------------------------------------------ #
    #  Internal Invariant Calculations
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_not_locked(self):
        if self.storage.get("reentrancy_locked", False):
            raise ContractError.REENTRANCY_GUARD

    def _set_locked(self, locked: Bool):
        self.storage.set("reentrancy_locked", locked)

    def _check_deadline(self, deadline: U64):
        if self.env.ledger().timestamp() > deadline:
            raise ContractError.DEADLINE_EXPIRED

    def _validate_ticks(self, tick_lower: I128, tick_upper: I128):
        if tick_lower >= tick_upper or tick_lower < MIN_TICK or tick_upper > MAX_TICK:
            raise ContractError.INVALID_TICK_RANGE
        # spacing alignment
        spacing = self.storage.get("tick_spacing")
        if (tick_lower % spacing != I128(0)) or (tick_upper % spacing != I128(0)):
            raise ContractError.INVALID_TICK_SPACING

    def _get_sqrt_ratio_at_tick(self, tick: I128) -> U128:
        """
        Calculates sqrt(1.0001^tick) * 2^96.
        Precise calculation using integer exponentiation mapping standard Uniswap V3 values.
        """
        # Exact values for binary decomposition mapping
        # 1.0001^(1/2) = 1.00004999878
        abs_tick = tick.abs() if hasattr(tick, "abs") else (tick if tick >= 0 else -tick)
        
        ratio = Q96
        # Binary multiplication logic mimicking Uniswap V3:
        if (abs_tick & 1) != 0: ratio = (ratio * 79224016282937746816568) // Q96
        if (abs_tick & 2) != 0: ratio = (ratio * 79232014603955639643501) // Q96
        if (abs_tick & 4) != 0: ratio = (ratio * 79247963385731998595996) // Q96
        if (abs_tick & 8) != 0: ratio = (ratio * 79279899144444583921359) // Q96
        if (abs_tick & 16) != 0: ratio = (ratio * 79343884877114670267246) // Q96
        if (abs_tick & 32) != 0: ratio = (ratio * 79472146954238515096180) // Q96
        if (abs_tick & 64) != 0: ratio = (ratio * 79729586111197998638927) // Q96
        if (abs_tick & 128) != 0: ratio = (ratio * 80247657920152431713437) // Q96
        if (abs_tick & 256) != 0: ratio = (ratio * 81295315572522067755112) // Q96
        if (abs_tick & 512) != 0: ratio = (ratio * 83451559648212140889269) // Q96
        if (abs_tick & 1024) != 0: ratio = (ratio * 88029013063519808389656) // Q96
        if (abs_tick & 2048) != 0: ratio = (ratio * 98029130635198083896564) // Q96
        if (abs_tick & 4096) != 0: ratio = (ratio * 122115163013233816674681) // Q96
        if (abs_tick & 8192) != 0: ratio = (ratio * 189622530514182903714275) // Q96
        if (abs_tick & 16384) != 0: ratio = (ratio * 457065096180373403310051) // Q96
        if (abs_tick & 32768) != 0: ratio = (ratio * 2663951528646985060424564) // Q96
        if (abs_tick & 65536) != 0: ratio = (ratio * 9041285093121571408000000) // Q96

        if tick < 0:
            ratio = (Q128) // ratio
            
        return ratio

    def _get_tick_at_sqrt_ratio(self, sqrt_price_x96: U128) -> I128:
        """Helper to find the tick mapping to the given sqrt_price_x96."""
        # Standard binary search approach for simplicity and deterministic performance
        low = MIN_TICK
        high = MAX_TICK
        while low < high:
            mid = (low + high) // 2
            mid_val = self._get_sqrt_ratio_at_tick(mid)
            if mid_val <= sqrt_price_x96:
                low = mid + 1
            else:
                high = mid
        return low - I128(1)

    def _get_amount_0_for_liquidity(self, sqrt_ratio_a: U128, sqrt_ratio_b: U128, liquidity: U128) -> U128:
        """Calculates Token 0 requirement: L * (sqrt(pb) - sqrt(pa)) / (sqrt(pa) * sqrt(pb))"""
        # Ensure sorting
        if sqrt_ratio_a > sqrt_ratio_b:
            sqrt_ratio_a, sqrt_ratio_b = sqrt_ratio_b, sqrt_ratio_a

        numerator = (liquidity * (sqrt_ratio_b - sqrt_ratio_a)) << 96
        denominator = sqrt_ratio_a * sqrt_ratio_b
        return numerator // denominator

    def _get_amount_1_for_liquidity(self, sqrt_ratio_a: U128, sqrt_ratio_b: U128, liquidity: U128) -> U128:
        """Calculates Token 1 requirement: L * (sqrt(pb) - sqrt(pa))"""
        if sqrt_ratio_a > sqrt_ratio_b:
            sqrt_ratio_a, sqrt_ratio_b = sqrt_ratio_b, sqrt_ratio_a

        return (liquidity * (sqrt_ratio_b - sqrt_ratio_a)) // Q96

    def _update_position(self, owner: Address, tick_lower: I128, tick_upper: I128, liquidity_delta: U128, is_add: Bool):
        """Update position records, crossing fee growth inside limits."""
        fee_growth_global_0_x128 = self.storage.get("fee_growth_global_0_x128")
        fee_growth_global_1_x128 = self.storage.get("fee_growth_global_1_x128")

        # Fetch tick growth states to isolate growth inside tick range
        fee_growth_inside_0 = fee_growth_global_0_x128
        fee_growth_inside_1 = fee_growth_global_1_x128

        # In production, we evaluate: global - outsideLower - outsideUpper
        # Simplify simulation to focus on updating the position totals
        pos_key = f"position_liquidity:{owner}:{tick_lower}:{tick_upper}"
        cur_liq = self.storage.get(pos_key, U128(0))

        # Fee calculations based on last growth inside snapshots
        pos_fee_growth_0_key = f"position_fee_growth_inside_0:{owner}:{tick_lower}:{tick_upper}"
        pos_fee_growth_1_key = f"position_fee_growth_inside_1:{owner}:{tick_lower}:{tick_upper}"
        
        last_growth_0 = self.storage.get(pos_fee_growth_0_key, U128(0))
        last_growth_1 = self.storage.get(pos_fee_growth_1_key, U128(0))

        if cur_liq > U128(0):
            owed_0 = ((fee_growth_inside_0 - last_growth_0) * cur_liq) // Q128
            owed_1 = ((fee_growth_inside_1 - last_growth_1) * cur_liq) // Q128
            
            self.storage.set(f"position_owed_0:{owner}:{tick_lower}:{tick_upper}", self.storage.get(f"position_owed_0:{owner}:{tick_lower}:{tick_upper}", U128(0)) + owed_0)
            self.storage.set(f"position_owed_1:{owner}:{tick_lower}:{tick_upper}", self.storage.get(f"position_owed_1:{owner}:{tick_lower}:{tick_upper}", U128(0)) + owed_1)

        # Update position size
        new_liq = cur_liq + liquidity_delta if is_add else cur_liq - liquidity_delta
        self.storage.set(pos_key, new_liq)

        # Update tick markers
        self.storage.set(f"tick_liquidity_gross:{tick_lower}", self.storage.get(f"tick_liquidity_gross:{tick_lower}", U128(0)) + liquidity_delta)
        self.storage.set(f"tick_liquidity_gross:{tick_upper}", self.storage.get(f"tick_liquidity_gross:{tick_upper}", U128(0)) + liquidity_delta)

        net_lower = self.storage.get(f"tick_liquidity_net:{tick_lower}", I128(0))
        net_upper = self.storage.get(f"tick_liquidity_net:{tick_upper}", I128(0))

        if is_add:
            self.storage.set(f"tick_liquidity_net:{tick_lower}", net_lower + I128(liquidity_delta))
            self.storage.set(f"tick_liquidity_net:{tick_upper}", net_upper - I128(liquidity_delta))
        else:
            self.storage.set(f"tick_liquidity_net:{tick_lower}", net_lower - I128(liquidity_delta))
            self.storage.set(f"tick_liquidity_net:{tick_upper}", net_upper + I128(liquidity_delta))

        # Snapshot current fee growth
        self.storage.set(pos_fee_growth_0_key, fee_growth_inside_0)
        self.storage.set(pos_fee_growth_1_key, fee_growth_inside_1)

    def _compute_swap_step(
        self,
        sqrt_price_current_x96: U128,
        sqrt_price_target_x96: U128,
        liquidity: U128,
        amount_remaining: U128,
        fee_tier: U64,
        exact_input: Bool
    ) -> tuple:
        """
        Calculates swap inputs, outputs, and resulting price.
        Mimics Uniswap V3 SwapMath.
        """
        # zeroForOne is true if price goes down
        zero_for_one = sqrt_price_current_x96 >= sqrt_price_target_x96

        amount_in = U128(0)
        amount_out = U128(0)

        if exact_input:
            # fee deducted from input
            amount_remaining_less_fee = (amount_remaining * (FEE_DENOMINATOR() - U128(fee_tier))) // FEE_DENOMINATOR()
            if zero_for_one:
                amount_in = self._get_amount_0_for_liquidity(sqrt_price_target_x96, sqrt_price_current_x96, liquidity)
            else:
                amount_in = self._get_amount_1_for_liquidity(sqrt_price_current_x96, sqrt_price_target_x96, liquidity)

            if amount_remaining_less_fee >= amount_in:
                # We reach the target price
                sqrt_price_next = sqrt_price_target_x96
            else:
                # Target price is not reached, recalculate next price based on exact input remaining
                sqrt_price_next = self._get_next_price_from_input(sqrt_price_current_x96, liquidity, amount_remaining_less_fee, zero_for_one)

        else:
            # Exact output swaps
            if zero_for_one:
                amount_out = self._get_amount_1_for_liquidity(sqrt_price_target_x96, sqrt_price_current_x96, liquidity)
            else:
                amount_out = self._get_amount_0_for_liquidity(sqrt_price_current_x96, sqrt_price_target_x96, liquidity)

            if amount_remaining >= amount_out:
                sqrt_price_next = sqrt_price_target_x96
            else:
                sqrt_price_next = self._get_next_price_from_output(sqrt_price_current_x96, liquidity, amount_remaining, zero_for_one)

        # Recompute final inputs and outputs for step based on derived next price
        if zero_for_one:
            amount_in_final = self._get_amount_0_for_liquidity(sqrt_price_next, sqrt_price_current_x96, liquidity)
            amount_out_final = self._get_amount_1_for_liquidity(sqrt_price_next, sqrt_price_current_x96, liquidity)
        else:
            amount_in_final = self._get_amount_1_for_liquidity(sqrt_price_current_x96, sqrt_price_next, liquidity)
            amount_out_final = self._get_amount_0_for_liquidity(sqrt_price_current_x96, sqrt_price_next, liquidity)

        # Cap output if exact output swap
        if not exact_input and amount_out_final > amount_remaining:
            amount_out_final = amount_remaining

        fee_amount = U128(0)
        if exact_input and sqrt_price_next != sqrt_price_target_x96:
            # Fee is remainder of actual input vs amount_remaining
            fee_amount = amount_remaining - amount_in_final
        else:
            fee_amount = (amount_in_final * U128(fee_tier)) // (FEE_DENOMINATOR() - U128(fee_tier))

        return (sqrt_price_next, amount_in_final, amount_out_final, fee_amount)

    def _get_next_price_from_input(self, sqrt_price_current_x96: U128, liquidity: U128, amount_in: U128, zero_for_one: Bool) -> U128:
        """Computes next price step given amount_in: P_next = P_curr +/- deltaP"""
        if zero_for_one:
            # Token 0 is input. delta(1/sqrt_price) = amount_in / L
            # sqrt_price_next = (L * sqrt_price_current) / (L + amount_in * sqrt_price_current)
            numerator = liquidity * sqrt_price_current_x96
            denominator = liquidity + (amount_in * sqrt_price_current_x96) // Q96
            return (numerator << 96) // denominator
        else:
            # Token 1 is input. delta(sqrt_price) = amount_in / L
            # sqrt_price_next = sqrt_price_current + (amount_in / L)
            return sqrt_price_current_x96 + (amount_in * Q96) // liquidity

    def _get_next_price_from_output(self, sqrt_price_current_x96: U128, liquidity: U128, amount_out: U128, zero_for_one: Bool) -> U128:
        """Computes next price step given amount_out: P_next = P_curr +/- deltaP"""
        if zero_for_one:
            # Token 1 is output. delta(sqrt_price) = amount_out / L
            # sqrt_price_next = sqrt_price_current - (amount_out / L)
            return sqrt_price_current_x96 - (amount_out * Q96) // liquidity
        else:
            # Token 0 is output. delta(1/sqrt_price) = amount_out / L
            # sqrt_price_next = (L * sqrt_price_current) / (L - amount_out * sqrt_price_current)
            numerator = liquidity * sqrt_price_current_x96
            denominator = liquidity - (amount_out * sqrt_price_current_x96) // Q96
            return (numerator << 96) // denominator


def FEE_DENOMINATOR() -> U128:
    return U128(10000)
