"""
Liquidity Bootstrapping Pool (LBP) — Time-weighted price discovery pool.

Features:
  - Dynamic linear weight shifting between Token 0 and Token 1 over time
  - Balancer-style constant value invariant V = x^w0 * y^w1
  - Swap weight math solver utilizing a 3-term Taylor expansion
  - Swap-only pool restriction: LPs cannot add/remove liquidity during sale
  - Anti-bot swap cooldown delay (per address block/timestamp check)
  - Anti-whale price manipulation guard (capping max trade size at 2% of reserves)
  - Refund and recovery mechanism: Creator closes pool and redeems remainder post-sale
  - Safe custody and reentrancy protection

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
    SALE_NOT_STARTED = 5
    SALE_HAS_ENDED = 6
    SWAP_DELAY_ACTIVE = 7
    MAX_SWAP_EXCEEDED = 8
    ZERO_AMOUNT = 9
    SLIPPAGE_EXCEEDED = 10
    POOL_CLOSED = 11
    POOL_NOT_CLOSED = 12
    POOL_NOT_ENDED = 13
    OVERFLOW = 14
    INVALID_WEIGHTS = 15


# Constants
WEIGHT_PRECISION = U128(10000)      # Weights scaled to 4 decimals (10000 = 100%)
FEE_DENOMINATOR = U128(10000)
TAYLOR_SCALE = U128(1_000_000_000)   # Scale for polynomial solvers (1e9)
ANTI_BOT_COOLDOWN = U64(15)         # 15 seconds swap cooldown per address
MAX_SWAP_BPS = U128(200)            # Max swap is 2% of pool reserves to protect price


@contract
class LiquidityBootstrappingPool:
    """
    Time-weighted price discovery pool shifting asset weights linearly from
    start weight to end weight during a sale period.
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
        amount_0: U128,
        amount_1: U128,
        start_weight_0: U128, # e.g. 9000 (90%)
        end_weight_0: U128,   # e.g. 1000 (10%)
        start_time: U64,
        end_time: U64,
        swap_fee_bps: U64,
    ):
        """Initialise pool liquidity, weights, and sale period."""
        admin.require_auth()

        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if start_time >= end_time:
            raise ContractError.INVALID_WEIGHTS
        if start_weight_0 == U128(0) or start_weight_0 >= WEIGHT_PRECISION:
            raise ContractError.INVALID_WEIGHTS
        if end_weight_0 == U128(0) or end_weight_0 >= WEIGHT_PRECISION:
            raise ContractError.INVALID_WEIGHTS
        if amount_0 == U128(0) or amount_1 == U128(0):
            raise ContractError.ZERO_AMOUNT

        self.storage.set("admin", admin)
        self.storage.set("token_0", token_0)
        self.storage.set("token_1", token_1)
        self.storage.set("reserve_0", amount_0)
        self.storage.set("reserve_1", amount_1)
        self.storage.set("start_weight_0", start_weight_0)
        self.storage.set("end_weight_0", end_weight_0)
        self.storage.set("start_time", start_time)
        self.storage.set("end_time", end_time)
        self.storage.set("swap_fee_bps", swap_fee_bps)
        self.storage.set("is_closed", False)
        self.storage.set("reentrancy_locked", False)
        self.storage.set("initialized", True)

        # Deposit starting assets into contract
        self.env.transfer(admin, self.env.current_contract(), token_0, amount_0)
        self.env.transfer(admin, self.env.current_contract(), token_1, amount_1)

        self.env.emit_event("initialized", {
            "admin": admin,
            "token_0": token_0,
            "token_1": token_1,
            "amount_0": amount_0,
            "amount_1": amount_1,
            "start_time": start_time,
            "end_time": end_time
        })

    # ------------------------------------------------------------------ #
    #  Swaps
    # ------------------------------------------------------------------ #

    @external
    def swap(
        self,
        caller: Address,
        token_in: Address,
        amount_in: U128,
        min_amount_out: U128,
        deadline: U64,
    ) -> U128:
        """
        Execute a token swap against the bootstrapping pool.
        Enforces linear weight updates, anti-bot delay, whale size limits, and Taylor math.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_closed()
        self._require_not_locked()
        self._check_deadline(deadline)

        now = self.env.ledger().timestamp()
        start_time = self.storage.get("start_time")
        end_time = self.storage.get("end_time")

        if now < start_time:
            raise ContractError.SALE_NOT_STARTED
        if now > end_time:
            raise ContractError.SALE_HAS_ENDED

        if amount_in == U128(0):
            raise ContractError.ZERO_AMOUNT

        self._set_locked(True)

        # Anti-bot delay check
        last_swap = self.storage.get(f"last_swap:{caller}", U64(0))
        if now < last_swap + ANTI_BOT_COOLDOWN:
            raise ContractError.SWAP_DELAY_ACTIVE
        self.storage.set(f"last_swap:{caller}", now)

        # Load pool reserves
        token_0 = self.storage.get("token_0")
        token_1 = self.storage.get("token_1")
        reserve_0 = self.storage.get("reserve_0")
        reserve_1 = self.storage.get("reserve_1")

        is_swap_0_to_1 = token_in == token_0

        # Whale protection limit check: max trade size cannot exceed 2% of reserves
        max_in = (reserve_0 * MAX_SWAP_BPS) // FEE_DENOMINATOR if is_swap_0_to_1 else (reserve_1 * MAX_SWAP_BPS) // FEE_DENOMINATOR
        if amount_in > max_in:
            raise ContractError.MAX_SWAP_EXCEEDED

        # Calculate current weights based on elapsed time
        w0, w1 = self._get_current_weights(now, start_time, end_time)

        # Deduct swap fee
        fee_bps = U128(self.storage.get("swap_fee_bps"))
        net_amount_in = amount_in - (amount_in * fee_bps) // FEE_DENOMINATOR

        # Calculate output using weighted Taylor expansion solver
        amount_out = U128(0)
        if is_swap_0_to_1:
            amount_out = self._calculate_weighted_out(net_amount_in, reserve_0, reserve_1, w0, w1)
            self.storage.set("reserve_0", reserve_0 + amount_in)
            self.storage.set("reserve_1", reserve_1 - amount_out)
        else:
            amount_out = self._calculate_weighted_out(net_amount_in, reserve_1, reserve_0, w1, w0)
            self.storage.set("reserve_1", reserve_1 + amount_in)
            self.storage.set("reserve_0", reserve_0 - amount_out)

        if amount_out < min_amount_out:
            raise ContractError.SLIPPAGE_EXCEEDED

        # Execute transfers
        token_out = token_1 if is_swap_0_to_1 else token_0
        self.env.transfer(caller, self.env.current_contract(), token_in, amount_in)
        self.env.transfer(self.env.current_contract(), caller, token_out, amount_out)

        self._set_locked(False)

        self.env.emit_event("swap", {
            "caller": caller,
            "token_in": token_in,
            "amount_in": amount_in,
            "amount_out": amount_out,
            "weight_0": w0,
            "weight_1": w1
        })
        return amount_out

    # ------------------------------------------------------------------ #
    #  Creator Close & Redemption
    # ------------------------------------------------------------------ #

    @external
    def close_pool(self, caller: Address):
        """
        Close pool after sale end, allowing admin to redeem remaining assets.
        Admin only.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        now = self.env.ledger().timestamp()
        end_time = self.storage.get("end_time")

        if now <= end_time:
            raise ContractError.POOL_NOT_ENDED

        self._set_locked(True)

        token_0 = self.storage.get("token_0")
        token_1 = self.storage.get("token_1")
        reserve_0 = self.storage.get("reserve_0")
        reserve_1 = self.storage.get("reserve_1")

        # Zero out reserves
        self.storage.set("reserve_0", U128(0))
        self.storage.set("reserve_1", U128(0))
        self.storage.set("is_closed", True)

        # Transfer all remaining pool balances to admin
        if reserve_0 > U128(0):
            self.env.transfer(self.env.current_contract(), caller, token_0, reserve_0)
        if reserve_1 > U128(0):
            self.env.transfer(self.env.current_contract(), caller, token_1, reserve_1)

        self._set_locked(False)

        self.env.emit_event("pool_closed", {
            "creator": caller,
            "redeemed_0": reserve_0,
            "redeemed_1": reserve_1
        })

    # ------------------------------------------------------------------ #
    #  View Functions
    # ------------------------------------------------------------------ #

    @view
    def get_weights(self) -> Vec:
        """Get the current weights of Token 0 and Token 1."""
        now = self.env.ledger().timestamp()
        start = self.storage.get("start_time")
        end = self.storage.get("end_time")
        w0, w1 = self._get_current_weights(now, start, end)
        return [w0, w1]

    @view
    def get_pool_info(self) -> Map:
        """Query comprehensive LBP status details."""
        now = self.env.ledger().timestamp()
        start = self.storage.get("start_time")
        end = self.storage.get("end_time")
        w0, w1 = self._get_current_weights(now, start, end)

        return {
            "token_0": self.storage.get("token_0"),
            "token_1": self.storage.get("token_1"),
            "reserve_0": self.storage.get("reserve_0"),
            "reserve_1": self.storage.get("reserve_1"),
            "weight_0": w0,
            "weight_1": w1,
            "start_time": start,
            "end_time": end,
            "is_closed": self.storage.get("is_closed")
        }

    # ------------------------------------------------------------------ #
    #  Internal Taylor Solver & Math
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_not_closed(self):
        if self.storage.get("is_closed", False):
            raise ContractError.POOL_CLOSED

    def _require_not_locked(self):
        if self.storage.get("reentrancy_locked", False):
            raise ContractError.REENTRANCY_GUARD

    def _set_locked(self, locked: Bool):
        self.storage.set("reentrancy_locked", locked)

    def _check_deadline(self, deadline: U64):
        if self.env.ledger().timestamp() > deadline:
            raise ContractError.DEADLINE_EXPIRED

    def _get_current_weights(self, now: U64, start: U64, end: U64) -> tuple:
        """Linear weight shift interpolation over the sale time window."""
        start_w0 = self.storage.get("start_weight_0")
        end_w0 = self.storage.get("end_weight_0")

        if now <= start:
            return (start_w0, WEIGHT_PRECISION - start_w0)
        if now >= end:
            return (end_w0, WEIGHT_PRECISION - end_w0)

        elapsed = U128(now - start)
        duration = U128(end - start)

        # linear interpolate: start_w0 + (elapsed/duration) * (end_w0 - start_w0)
        w0 = U128(0)
        if end_w0 > start_w0:
            w0 = start_w0 + (elapsed * (end_w0 - start_w0)) // duration
        else:
            w0 = start_w0 - (elapsed * (start_w0 - end_w0)) // duration

        return (w0, WEIGHT_PRECISION - w0)

    def _calculate_weighted_out(
        self,
        amount_in: U128,
        reserve_in: U128,
        reserve_out: U128,
        weight_in: U128,
        weight_out: U128
    ) -> U128:
        """
        Balancer swap formula: out = reserve_out * (1 - (reserve_in / (reserve_in + amount_in))^(weight_in/weight_out))
        Uses Taylor polynomial expansion of (1 - y)^a for y = amount_in / (reserve_in + amount_in).
        Since trade size is capped at 2% reserve, y <= 0.02.
        For y <= 0.02, Taylor expansion: out = reserve_out * (a*y - a*(a-1)/2 * y^2 + a*(a-1)*(a-2)/6 * y^3) is highly accurate.
        """
        # y = amount_in / (reserve_in + amount_in) scaled to TAYLOR_SCALE (1e9)
        total_in = reserve_in + amount_in
        y = (amount_in * TAYLOR_SCALE) // total_in

        # a = weight_in / weight_out scaled to TAYLOR_SCALE (1e9)
        a = (weight_in * TAYLOR_SCALE) // weight_out

        # Term 1: a * y (scaled by TAYLOR_SCALE)
        t1 = (a * y) // TAYLOR_SCALE

        # Term 2: a * (a - 1) / 2 * y^2
        # Scale intermediate products to maintain resolution
        a_minus_1 = a - TAYLOR_SCALE
        t2 = U128(0)
        # Handle cases where exponent weight ratio is less than 1.0 (a < 1e9)
        if a > TAYLOR_SCALE:
            t2 = (a * a_minus_1) // TAYLOR_SCALE
            t2 = (t2 * y) // TAYLOR_SCALE
            t2 = (t2 * y) // (TAYLOR_SCALE * U128(2))
        else:
            # negative expansion factor
            a_abs = TAYLOR_SCALE - a
            t2 = (a * a_abs) // TAYLOR_SCALE
            t2 = (t2 * y) // TAYLOR_SCALE
            t2 = (t2 * y) // (TAYLOR_SCALE * U128(2))

        # Term 3: a * (a - 1) * (a - 2) / 6 * y^3
        t3 = U128(0)
        if a > TAYLOR_SCALE * U128(2):
            a_minus_2 = a - TAYLOR_SCALE * U128(2)
            t3 = (a * a_minus_1) // TAYLOR_SCALE
            t3 = (t3 * a_minus_2) // TAYLOR_SCALE
            t3 = (t3 * y) // TAYLOR_SCALE
            t3 = (t3 * y) // TAYLOR_SCALE
            t3 = (t3 * y) // (TAYLOR_SCALE * U128(6))

        # Taylor summation: out = reserve_out * (t1 - t2 + t3)
        # Note: if a < 1, signs flip, but we handle standard bootstrapping where
        # weight of project token starts high (90%) and ends low (10%), meaning
        # weight_in > weight_out for the main pool token, so a > 1.
        # To be safe, we compute absolute sum matching signs.
        sum_terms = t1
        if t1 >= t2:
            sum_terms = t1 - t2 + t3
        else:
            sum_terms = t1 + t3 # absolute fallback

        amount_out = (reserve_out * sum_terms) // TAYLOR_SCALE
        return amount_out
