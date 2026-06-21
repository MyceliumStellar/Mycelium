"""
StableSwap AMM — Curve-style StableSwap with configurable amplification parameter (A).

Features:
  - Multi-asset pool support (2-4 assets)
  - Newton-Raphson solvers for StableSwap invariant D and y calculations
  - Imbalanced add/remove liquidity
  - Single token liquidity withdrawal
  - Virtual price tracking (D / total_supply)
  - Configurable amplification parameter (A) with admin controls
  - Admin fee collection (percentage of swap fees)
  - Emergency kill switch (pauses swaps and liquidity addition)
  - Reentrancy protection and transaction deadlines

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
    KILLED = 5
    DEADLINE_EXPIRED = 6
    INVALID_ASSET_COUNT = 7
    INVALID_ASSET_INDEX = 8
    ZERO_AMOUNT = 9
    INSUFFICIENT_LP_BALANCE = 10
    SLIPPAGE_EXCEEDED = 11
    D_CONVERGENCE_FAILED = 12
    Y_CONVERGENCE_FAILED = 13
    OVERFLOW = 14
    INSUFFICIENT_FEE = 15
    INVALID_COEFF = 16
    ZERO_RESERVES = 17


# Constants
A_PRECISION = U128(100)
FEE_DENOMINATOR = U128(10000)
MAX_A = U128(1_000_000)
MAX_FEE = U128(500)  # 5% max fee
MAX_ADMIN_FEE = U128(10000)  # 100% of swap fee


@contract
class StableSwapAMM:
    """
    Curve-style StableSwap contract for multi-asset pools with advanced invariant math,
    dynamic balances, imbalanced LP interactions, and admin features.
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
        tokens: Vec,
        A: U128,
        fee: U128,
        admin_fee: U128,
    ):
        """
        Set up the StableSwap pool.

        Args:
            admin: Admin address.
            tokens: Vector of 2-4 token Addresses.
            A: Amplification coefficient multiplied by A_PRECISION (100).
            fee: Swap fee in basis points (e.g. 4 = 0.04%).
            admin_fee: Fraction of fee that goes to admin (e.g. 5000 = 50%).
        """
        admin.require_auth()

        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        n_tokens = len(tokens)
        if n_tokens < 2 or n_tokens > 4:
            raise ContractError.INVALID_ASSET_COUNT

        if A == U128(0) or A > MAX_A:
            raise ContractError.INVALID_COEFF
        if fee > MAX_FEE or admin_fee > MAX_ADMIN_FEE:
            raise ContractError.INSUFFICIENT_FEE

        self.storage.set("admin", admin)
        self.storage.set("tokens", tokens)
        self.storage.set("n_tokens", U128(n_tokens))
        self.storage.set("A", A)
        self.storage.set("fee", fee)
        self.storage.set("admin_fee", admin_fee)
        self.storage.set("total_supply", U128(0))
        self.storage.set("killed", False)
        self.storage.set("reentrancy_locked", False)
        self.storage.set("initialized", True)

        # Initialize reserves and admin fee balances
        for i in range(n_tokens):
            self.storage.set(f"reserve:{i}", U128(0))
            self.storage.set(f"admin_fee_accumulated:{i}", U128(0))

        self.env.emit_event("initialized", {
            "admin": admin,
            "tokens": tokens,
            "A": A,
            "fee": fee,
            "admin_fee": admin_fee
        })

    # ------------------------------------------------------------------ #
    #  Liquidity Provision
    # ------------------------------------------------------------------ #

    @external
    def add_liquidity(
        self,
        provider: Address,
        amounts: Vec,
        min_mint_amount: U128,
        deadline: U64,
    ) -> U128:
        """
        Add liquidity to the pool. Can accept imbalanced amounts.

        Args:
            provider: Account providing liquidity.
            amounts: Amounts of each token in order. Must match token list length.
            min_mint_amount: Slippage check: minimum LP tokens to mint.
            deadline: Ledger timestamp expiration.

        Returns:
            Amount of LP tokens minted.
        """
        provider.require_auth()
        self._require_initialized()
        self._require_not_killed()
        self._require_not_locked()
        self._check_deadline(deadline)
        self._set_locked(True)

        n_tokens = int(self.storage.get("n_tokens"))
        if len(amounts) != n_tokens:
            raise ContractError.INVALID_ASSET_COUNT

        tokens = self.storage.get("tokens")
        A = self.storage.get("A")
        fee = self.storage.get("fee")
        admin_fee = self.storage.get("admin_fee")
        total_supply = self.storage.get("total_supply")

        # Gather old reserves and compute old D
        old_balances = self._get_reserves_vec(n_tokens)
        D0 = U128(0)
        if total_supply > U128(0):
            D0 = self._get_D(old_balances, A)

        new_balances = []
        has_nonzero = False
        for i in range(n_tokens):
            amt = amounts[i]
            if amt > U128(0):
                has_nonzero = True
                # Transfer token to contract
                self.env.transfer(provider, self.env.current_contract(), tokens[i], amt)
            new_balances.append(old_balances[i] + amt)

        if not has_nonzero:
            raise ContractError.ZERO_AMOUNT

        # Compute new D
        D1 = self._get_D(new_balances, A)
        if D1 <= D0:
            raise ContractError.D_CONVERGENCE_FAILED

        # Calculate fees for imbalanced deposit
        mint_amount = U128(0)
        fees = []
        for i in range(n_tokens):
            fees.append(U128(0))

        if total_supply > U128(0):
            # Only charge fees on the difference from proportional deposit
            # fraction to deposit = (D1 - D0) / D0
            # expected_i = old_balances[i] * D1 / D0
            # difference_i = abs(expected_i - new_balances[i])
            # fee_i = difference_i * fee / (10000 * 2) (Curve style)
            d_lp = D1 - D0
            for i in range(n_tokens):
                ideal_balance = (old_balances[i] * D1) // D0
                difference = U128(0)
                if ideal_balance > new_balances[i]:
                    difference = ideal_balance - new_balances[i]
                else:
                    difference = new_balances[i] - ideal_balance

                # curve fee factor: n * fee / (4 * (n - 1))
                fee_coeff = (U128(n_tokens) * fee) // (U128(4) * U128(n_tokens - 1))
                fees[i] = (difference * fee_coeff) // FEE_DENOMINATOR

                admin_share = (fees[i] * admin_fee) // FEE_DENOMINATOR
                accumulated_admin = self.storage.get(f"admin_fee_accumulated:{i}")
                self.storage.set(f"admin_fee_accumulated:{i}", accumulated_admin + admin_share)

                new_balances[i] = new_balances[i] - admin_share

            # Recompute D with adjusted balances
            D2 = self._get_D(new_balances, A)
            mint_amount = (total_supply * (D2 - D0)) // D0
        else:
            # First deposit: mint amount is exactly D1
            mint_amount = D1

        if mint_amount < min_mint_amount:
            raise ContractError.SLIPPAGE_EXCEEDED

        # Update reserves
        for i in range(n_tokens):
            self.storage.set(f"reserve:{i}", new_balances[i])

        # Mint LP tokens
        provider_lp = self.storage.get(("lp_balance", provider), U128(0))
        self.storage.set(("lp_balance", provider), provider_lp + mint_amount)
        self.storage.set("total_supply", total_supply + mint_amount)

        self._set_locked(False)

        self.env.emit_event("liquidity_added", {
            "provider": provider,
            "amounts": amounts,
            "lp_minted": mint_amount,
            "new_supply": total_supply + mint_amount
        })
        return mint_amount

    @external
    def remove_liquidity(
        self,
        provider: Address,
        lp_amount: U128,
        min_amounts: Vec,
        deadline: U64,
    ) -> Vec:
        """
        Remove liquidity proportionally. No fee incurred.

        Args:
            provider: Account withdrawing liquidity.
            lp_amount: Amount of LP tokens to burn.
            min_amounts: Minimum acceptable token withdrawal amounts.
            deadline: Ledger timestamp expiration.

        Returns:
            Vector of withdrawn amounts.
        """
        provider.require_auth()
        self._require_initialized()
        self._require_not_locked()
        self._check_deadline(deadline)
        self._set_locked(True)

        n_tokens = int(self.storage.get("n_tokens"))
        if len(min_amounts) != n_tokens:
            raise ContractError.INVALID_ASSET_COUNT

        total_supply = self.storage.get("total_supply")
        provider_lp = self.storage.get(("lp_balance", provider), U128(0))
        if provider_lp < lp_amount:
            raise ContractError.INSUFFICIENT_LP_BALANCE

        tokens = self.storage.get("tokens")
        amounts_out = []

        for i in range(n_tokens):
            reserve = self.storage.get(f"reserve:{i}")
            amt = (reserve * lp_amount) // total_supply
            if amt < min_amounts[i]:
                raise ContractError.SLIPPAGE_EXCEEDED

            amounts_out.append(amt)
            self.storage.set(f"reserve:{i}", reserve - amt)
            self.env.transfer(self.env.current_contract(), provider, tokens[i], amt)

        self.storage.set(("lp_balance", provider), provider_lp - lp_amount)
        self.storage.set("total_supply", total_supply - lp_amount)

        self._set_locked(False)

        self.env.emit_event("liquidity_removed", {
            "provider": provider,
            "lp_burned": lp_amount,
            "amounts_out": amounts_out,
            "new_supply": total_supply - lp_amount
        })
        return amounts_out

    @external
    def remove_liquidity_one_coin(
        self,
        provider: Address,
        lp_amount: U128,
        token_index: U64,
        min_amount: U128,
        deadline: U64,
    ) -> U128:
        """
        Remove liquidity in a single token. Incurs swapping fees.

        Args:
            provider: Account withdrawing liquidity.
            lp_amount: LP tokens to burn.
            token_index: Index of token to receive.
            min_amount: Minimum acceptable amount of target token.
            deadline: Ledger timestamp expiration.

        Returns:
            Amount of token received.
        """
        provider.require_auth()
        self._require_initialized()
        self._require_not_locked()
        self._check_deadline(deadline)
        self._set_locked(True)

        n_tokens = int(self.storage.get("n_tokens"))
        if token_index >= n_tokens:
            raise ContractError.INVALID_ASSET_INDEX

        total_supply = self.storage.get("total_supply")
        provider_lp = self.storage.get(("lp_balance", provider), U128(0))
        if provider_lp < lp_amount:
            raise ContractError.INSUFFICIENT_LP_BALANCE

        A = self.storage.get("A")
        fee = self.storage.get("fee")
        admin_fee = self.storage.get("admin_fee")
        tokens = self.storage.get("tokens")

        # Solve for virtual D before burn
        balances = self._get_reserves_vec(n_tokens)
        D0 = self._get_D(balances, A)

        # Target D post burn
        D1 = D0 - (lp_amount * D0) // total_supply

        # Solve for new balance of target token at D1
        new_y = self._get_y_D(A, int(token_index), balances, D1)
        
        # Calculate fees
        dy_expected = balances[int(token_index)] - new_y
        dy = U128(0)
        
        for i in range(n_tokens):
            # calculate ideal balance change
            ideal_balance = (balances[i] * D1) // D0
            difference = U128(0)
            if i == int(token_index):
                difference = balances[i] - ideal_balance - dy_expected if (balances[i] - ideal_balance) > dy_expected else dy_expected - (balances[i] - ideal_balance)
            else:
                difference = balances[i] - ideal_balance if balances[i] > ideal_balance else ideal_balance - balances[i]

            fee_coeff = (U128(n_tokens) * fee) // (U128(4) * U128(n_tokens - 1))
            fee_i = (difference * fee_coeff) // FEE_DENOMINATOR
            admin_share = (fee_i * admin_fee) // FEE_DENOMINATOR
            
            accumulated_admin = self.storage.get(f"admin_fee_accumulated:{i}")
            self.storage.set(f"admin_fee_accumulated:{i}", accumulated_admin + admin_share)
            
            balances[i] = balances[i] - admin_share
            if i == int(token_index):
                dy_expected = dy_expected - (fee_i - admin_share)

        # Re-solve y with adjusted balances
        dy = balances[int(token_index)] - self._get_y_D(A, int(token_index), balances, D1)

        if dy < min_amount:
            raise ContractError.SLIPPAGE_EXCEEDED

        # Update target reserve
        self.storage.set(f"reserve:{token_index}", balances[int(token_index)] - dy)
        # Update other reserves due to admin fee updates
        for i in range(n_tokens):
            if i != int(token_index):
                self.storage.set(f"reserve:{i}", balances[i])

        # Burn LP
        self.storage.set(("lp_balance", provider), provider_lp - lp_amount)
        self.storage.set("total_supply", total_supply - lp_amount)

        # Send token
        self.env.transfer(self.env.current_contract(), provider, tokens[token_index], dy)

        self._set_locked(False)

        self.env.emit_event("liquidity_removed_one", {
            "provider": provider,
            "lp_burned": lp_amount,
            "token_index": token_index,
            "amount_out": dy
        })
        return dy

    # ------------------------------------------------------------------ #
    #  Swaps
    # ------------------------------------------------------------------ #

    @external
    def swap(
        self,
        caller: Address,
        i: U64,
        j: U64,
        dx: U128,
        min_dy: U128,
        deadline: U64,
    ) -> U128:
        """
        Swap asset i for asset j.

        Args:
            caller: Account performing the swap.
            i: Index of the input token.
            j: Index of the output token.
            dx: Amount of input token to swap.
            min_dy: Slippage check: minimum acceptable output.
            deadline: Ledger timestamp expiration.

        Returns:
            Amount of output token sent.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_killed()
        self._require_not_locked()
        self._check_deadline(deadline)
        self._set_locked(True)

        n_tokens = int(self.storage.get("n_tokens"))
        if i >= n_tokens or j >= n_tokens or i == j:
            raise ContractError.INVALID_ASSET_INDEX
        if dx == U128(0):
            raise ContractError.ZERO_AMOUNT

        tokens = self.storage.get("tokens")
        A = self.storage.get("A")
        fee = self.storage.get("fee")
        admin_fee = self.storage.get("admin_fee")

        balances = self._get_reserves_vec(n_tokens)

        # Solve for new balance of token j
        x = balances[int(i)] + dx
        y = self._get_y(i, j, x, balances, A)

        dy = balances[int(j)] - y
        
        # Calculate fees
        fee_amt = (dy * fee) // FEE_DENOMINATOR
        admin_share = (fee_amt * admin_fee) // FEE_DENOMINATOR

        dy_net = dy - fee_amt
        if dy_net < min_dy:
            raise ContractError.SLIPPAGE_EXCEEDED

        # Accumulate admin fees and update reserves
        self.storage.set(f"admin_fee_accumulated:{j}", self.storage.get(f"admin_fee_accumulated:{j}") + admin_share)
        self.storage.set(f"reserve:{i}", x)
        self.storage.set(f"reserve:{j}", balances[int(j)] - dy_net - admin_share)

        # Execute transfers
        self.env.transfer(caller, self.env.current_contract(), tokens[i], dx)
        self.env.transfer(self.env.current_contract(), caller, tokens[j], dy_net)

        self._set_locked(False)

        self.env.emit_event("swap", {
            "caller": caller,
            "token_in_idx": i,
            "token_out_idx": j,
            "amount_in": dx,
            "amount_out": dy_net
        })
        return dy_net

    # ------------------------------------------------------------------ #
    #  Admin & Configuration Functions
    # ------------------------------------------------------------------ #

    @external
    def set_a(self, caller: Address, new_A: U128):
        """Update amplification parameter (A). Admin only."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        if new_A == U128(0) or new_A > MAX_A:
            raise ContractError.INVALID_COEFF

        old_A = self.storage.get("A")
        self.storage.set("A", new_A)

        self.env.emit_event("amplification_updated", {
            "old_A": old_A,
            "new_A": new_A
        })

    @external
    def set_fees(self, caller: Address, fee: U128, admin_fee: U128):
        """Update fees. Admin only."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        if fee > MAX_FEE or admin_fee > MAX_ADMIN_FEE:
            raise ContractError.INSUFFICIENT_FEE

        self.storage.set("fee", fee)
        self.storage.set("admin_fee", admin_fee)

        self.env.emit_event("fees_updated", {
            "fee": fee,
            "admin_fee": admin_fee
        })

    @external
    def set_kill_switch(self, caller: Address, killed: Bool):
        """Pause/resume pool swaps and liquidity additions. Admin only."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        self.storage.set("killed", killed)

        self.env.emit_event("kill_switch_toggled", {
            "killed": killed
        })

    @external
    def collect_admin_fees(self, caller: Address, to: Address):
        """Collect accumulated admin fees. Admin only."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        n_tokens = int(self.storage.get("n_tokens"))
        tokens = self.storage.get("tokens")

        collected = []
        for i in range(n_tokens):
            amt = self.storage.get(f"admin_fee_accumulated:{i}")
            if amt > U128(0):
                self.storage.set(f"admin_fee_accumulated:{i}", U128(0))
                self.env.transfer(self.env.current_contract(), to, tokens[i], amt)
            collected.append(amt)

        self.env.emit_event("admin_fees_collected", {
            "to": to,
            "collected": collected
        })

    # ------------------------------------------------------------------ #
    #  View Functions
    # ------------------------------------------------------------------ #

    @view
    def get_virtual_price(self) -> U128:
        """Get the virtual price (value of 1 LP token in terms of pool assets)."""
        total_supply = self.storage.get("total_supply", U128(0))
        if total_supply == U128(0):
            return U128(0)

        n_tokens = int(self.storage.get("n_tokens"))
        balances = self._get_reserves_vec(n_tokens)
        A = self.storage.get("A")
        D = self._get_D(balances, A)

        # Scale by 1e18 for precision
        return (D * U128(1_000_000_000_000_000_000)) // total_supply

    @view
    def get_dy(self, i: U64, j: U64, dx: U128) -> U128:
        """Estimate output amount for swap of dx from token i to j."""
        n_tokens = int(self.storage.get("n_tokens"))
        if i >= n_tokens or j >= n_tokens or i == j:
            raise ContractError.INVALID_ASSET_INDEX

        balances = self._get_reserves_vec(n_tokens)
        A = self.storage.get("A")
        fee = self.storage.get("fee")

        x = balances[int(i)] + dx
        y = self._get_y(i, j, x, balances, A)

        dy = balances[int(j)] - y
        return dy - (dy * fee) // FEE_DENOMINATOR

    @view
    def get_reserves(self) -> Vec:
        """Return the reserves list."""
        n_tokens = int(self.storage.get("n_tokens"))
        return self._get_reserves_vec(n_tokens)

    @view
    def get_lp_balance(self, account: Address) -> U128:
        """Return the LP token balance of an account."""
        return self.storage.get(("lp_balance", account), U128(0))

    @view
    def get_pool_info(self) -> Map:
        """Return comprehensive metadata about the StableSwap pool."""
        n_tokens = int(self.storage.get("n_tokens"))
        admin_fees = []
        for i in range(n_tokens):
            admin_fees.append(self.storage.get(f"admin_fee_accumulated:{i}"))

        return {
            "tokens": self.storage.get("tokens"),
            "reserves": self._get_reserves_vec(n_tokens),
            "A": self.storage.get("A"),
            "fee": self.storage.get("fee"),
            "admin_fee": self.storage.get("admin_fee"),
            "total_supply": self.storage.get("total_supply"),
            "admin_fees_accumulated": admin_fees,
            "killed": self.storage.get("killed"),
        }

    # ------------------------------------------------------------------ #
    #  Private Invariant Solvers
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_not_killed(self):
        if self.storage.get("killed", False):
            raise ContractError.KILLED

    def _require_not_locked(self):
        if self.storage.get("reentrancy_locked", False):
            raise ContractError.REENTRANCY_GUARD

    def _set_locked(self, locked: Bool):
        self.storage.set("reentrancy_locked", locked)

    def _check_deadline(self, deadline: U64):
        if self.env.ledger().timestamp() > deadline:
            raise ContractError.DEADLINE_EXPIRED

    def _get_reserves_vec(self, n_tokens: int) -> list:
        res = []
        for i in range(n_tokens):
            res.append(self.storage.get(f"reserve:{i}"))
        return res

    def _get_D(self, xp: list, A: U128) -> U128:
        """
        Solve for D using Newton's method.
        xp: list of token balances (scaled if necessary, here assumed equal decimals)
        A: amplification coefficient * A_PRECISION
        """
        n = U128(len(xp))
        S = U128(0)
        for x in xp:
            S += x

        if S == U128(0):
            return U128(0)

        Dprev = U128(0)
        D = S

        # Ann = A * n^n
        Ann = A
        for _ in range(int(n)):
            Ann = Ann * n
        Ann = Ann // A_PRECISION

        for _ in range(255):
            Dp = D
            for x in xp:
                Dp = (Dp * D) // (x * n)

            Dprev = D
            # D = (Ann * S + Dp * n) * D / ((Ann - 1) * D + (n + 1) * Dp)
            numerator = D * (Ann * S + Dp * n)
            denominator = D * (Ann - U128(1)) + Dp * (n + U128(1))
            D = numerator // denominator

            if D > Dprev:
                if D - Dprev <= U128(1):
                    return D
            else:
                if Dprev - D <= U128(1):
                    return D

        raise ContractError.D_CONVERGENCE_FAILED

    def _get_y(self, i: U64, j: U64, x: U128, xp: list, A: U128) -> U128:
        """
        Solve for y (balance of token j) if token i balance becomes x.
        """
        n = U128(len(xp))
        if i >= n or j >= n or i == j:
            raise ContractError.INVALID_ASSET_INDEX

        D = self._get_D(xp, A)

        Ann = A
        for _ in range(int(n)):
            Ann = Ann * n
        Ann = Ann // A_PRECISION

        c = D
        S_ = U128(0)
        for k in range(int(n)):
            if k == int(i):
                xx = x
            elif k == int(j):
                continue
            else:
                xx = xp[k]
            
            S_ += xx
            c = (c * D) // (xx * n)

        # c = c * D / (Ann * n^n)
        c = (c * D * A_PRECISION) // (Ann * n)
        b = S_ + (D * A_PRECISION) // Ann
        y_prev = U128(0)
        y = D

        for _ in range(255):
            y_prev = y
            # y = (y^2 + c) / (2*y + b - D)
            y = (y * y + c) // (y * U128(2) + b - D)

            if y > y_prev:
                if y - y_prev <= U128(1):
                    return y
            else:
                if y_prev - y <= U128(1):
                    return y

        raise ContractError.Y_CONVERGENCE_FAILED

    def _get_y_D(self, A: U128, i: int, xp: list, D: U128) -> U128:
        """
        Solve for balance of token i given target invariant value D.
        """
        n = U128(len(xp))
        Ann = A
        for _ in range(int(n)):
            Ann = Ann * n
        Ann = Ann // A_PRECISION

        c = D
        S_ = U128(0)
        for k in range(int(n)):
            if k == i:
                continue
            xx = xp[k]
            S_ += xx
            c = (c * D) // (xx * n)

        c = (c * D * A_PRECISION) // (Ann * n)
        b = S_ + (D * A_PRECISION) // Ann
        y_prev = U128(0)
        y = D

        for _ in range(255):
            y_prev = y
            y = (y * y + c) // (y * U128(2) + b - D)

            if y > y_prev:
                if y - y_prev <= U128(1):
                    return y
            else:
                if y_prev - y <= U128(1):
                    return y

        raise ContractError.Y_CONVERGENCE_FAILED
