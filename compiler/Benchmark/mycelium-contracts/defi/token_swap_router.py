"""
Token Swap Router — Multi-hop and split-route path-based swap router.

Features:
  - Path-based multi-hop swaps (e.g. A -> B -> C)
  - Split routes supporting proportional division across paths (e.g. 60% path 1, 40% path 2)
  - Integration with Constant Product AMMs and StableSwap AMMs
  - Dynamic token index lookup for StableSwap pools
  - Strict transaction deadline enforcement
  - Global slippage validation at the final output token level
  - Safe custody and routing transfer mechanics
  - Reentrancy protection

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
    SLIPPAGE_EXCEEDED = 6
    INVALID_PATH = 7
    TOKEN_INDEX_NOT_FOUND = 8
    INVALID_ALLOCATION = 9
    POOL_SWAP_FAILED = 10


# Constants
ALLOCATION_DENOMINATOR = U128(10000)  # Basis points


@contract
class TokenSwapRouter:
    """
    Multi-hop swap router supporting split-route trade execution across both
    constant product AMMs and stableswap pools.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ------------------------------------------------------------------ #
    #  Initialization
    # ------------------------------------------------------------------ #

    @external
    def initialize(self, admin: Address):
        """Initialise router administration."""
        admin.require_auth()

        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("reentrancy_locked", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin
        })

    # ------------------------------------------------------------------ #
    #  Swap Router Functions
    # ------------------------------------------------------------------ #

    @external
    def swap_exact_input_multihop(
        self,
        caller: Address,
        path_tokens: Vec,  # Vec of token Addresses
        path_pools: Vec,   # Vec of pool Addresses
        pool_modes: Vec,   # Vec of modes (1: Constant Product, 2: StableSwap)
        amount_in: U128,
        min_amount_out: U128,
        deadline: U64,
    ) -> U128:
        """
        Perform a sequential multi-hop swap along a single path.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_locked()
        self._check_deadline(deadline)
        self._set_locked(True)

        n_pools = len(path_pools)
        if n_pools == 0 or len(path_tokens) != n_pools + 1 or len(pool_modes) != n_pools:
            raise ContractError.INVALID_PATH

        # Pull input token from caller
        first_token = path_tokens[0]
        self.env.transfer(caller, self.env.current_contract(), first_token, amount_in)

        current_amount = amount_in

        # Loop through each pool in the hop path
        for k in range(n_pools):
            pool = path_pools[k]
            mode = int(pool_modes[k])
            token_in = path_tokens[k]
            token_out = path_tokens[k + 1]

            current_amount = self._execute_pool_swap(
                pool, mode, token_in, token_out, current_amount, deadline
            )

        # Slippage check
        if current_amount < min_amount_out:
            raise ContractError.SLIPPAGE_EXCEEDED

        # Transfer final output token to caller
        last_token = path_tokens[n_pools]
        self.env.transfer(self.env.current_contract(), caller, last_token, current_amount)

        self._set_locked(False)

        self.env.emit_event("multihop_swap_completed", {
            "caller": caller,
            "token_in": first_token,
            "token_out": last_token,
            "amount_in": amount_in,
            "amount_out": current_amount
        })
        return current_amount

    @external
    def swap_exact_input_split(
        self,
        caller: Address,
        paths_tokens: Vec,       # Vec of Vec of token Addresses
        paths_pools: Vec,        # Vec of Vec of pool Addresses
        paths_modes: Vec,        # Vec of Vec of modes
        paths_allocations: Vec,  # Vec of allocation ratios in bps
        amount_in: U128,
        min_amount_out: U128,
        deadline: U64,
    ) -> U128:
        """
        Split amount_in across multiple routing paths proportionally.
        Gathers output tokens and performs a final slippage check.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_locked()
        self._check_deadline(deadline)
        self._set_locked(True)

        n_paths = len(paths_tokens)
        if n_paths == 0 or len(paths_pools) != n_paths or len(paths_modes) != n_paths or len(paths_allocations) != n_paths:
            raise ContractError.INVALID_PATH

        # Validate total split allocations equals 100% (10000 bps)
        total_bps = U128(0)
        for i in range(n_paths):
            total_bps += paths_allocations[i]
        if total_bps != ALLOCATION_DENOMINATOR:
            raise ContractError.INVALID_ALLOCATION

        # Pull input token from caller
        first_token = paths_tokens[0][0]
        self.env.transfer(caller, self.env.current_contract(), first_token, amount_in)

        # Output asset must be identical across all paths
        last_token = paths_tokens[0][len(paths_tokens[0]) - 1]
        
        total_output_acquired = U128(0)

        for i in range(n_paths):
            path_tokens = paths_tokens[i]
            path_pools = paths_pools[i]
            pool_modes = paths_modes[i]
            alloc_bps = paths_allocations[i]

            if path_tokens[0] != first_token or path_tokens[len(path_tokens) - 1] != last_token:
                raise ContractError.INVALID_PATH

            # Calculate slice size for this path
            path_amount_in = (amount_in * alloc_bps) // ALLOCATION_DENOMINATOR
            if path_amount_in == U128(0):
                continue

            current_amount = path_amount_in
            n_pools = len(path_pools)

            # Route through hops
            for k in range(n_pools):
                pool = path_pools[k]
                mode = int(pool_modes[k])
                token_in = path_tokens[k]
                token_out = path_tokens[k + 1]

                current_amount = self._execute_pool_swap(
                    pool, mode, token_in, token_out, current_amount, deadline
                )

            total_output_acquired += current_amount

        # Final slippage check
        if total_output_acquired < min_amount_out:
            raise ContractError.SLIPPAGE_EXCEEDED

        # Send final token to user
        self.env.transfer(self.env.current_contract(), caller, last_token, total_output_acquired)

        self._set_locked(False)

        self.env.emit_event("split_swap_completed", {
            "caller": caller,
            "token_in": first_token,
            "token_out": last_token,
            "amount_in": amount_in,
            "amount_out": total_output_acquired
        })
        return total_output_acquired

    # ------------------------------------------------------------------ #
    #  Internal Pool Swapping Execution
    # ------------------------------------------------------------------ #

    def _execute_pool_swap(
        self,
        pool: Address,
        mode: int,
        token_in: Address,
        token_out: Address,
        amount_in: U128,
        deadline: U64
    ) -> U128:
        """
        Executes swap on targeted pool.
        Adapts calls for Constant Product pools (mode 1) and StableSwap pools (mode 2).
        """
        if mode == 1:
            # Mode 1: Constant Product AMM
            # Router must first authorize the pool or transfer tokens directly depending on pool requirement
            # Let's transfer tokens directly to the pool first, as standard Uniswap V2 router does,
            # or call swap assuming pool has transfer capabilities.
            # In our Constant Product AMM code, the pool pulls tokens from 'caller'.
            # Therefore, we call swap_exact_input with the router as the 'caller'.
            # We must authorize the swap. Since router environment invokes this, router is transaction caller.
            # Let's transfer/approve first:
            # We call the AMM swap_exact_input:
            out_amt = self.env.invoke_contract(pool, "swap_exact_input", [
                self.env.current_contract(), # caller for pool is router
                token_in,
                amount_in,
                U128(1), # min output checked globally at end of path
                deadline
            ])
            return out_amt

        elif mode == 2:
            # Mode 2: StableSwap Pool
            # We need to find token indexes (i and j) by querying pool info
            pool_info = self.env.invoke_contract(pool, "get_pool_info", [])
            tokens = pool_info["tokens"]

            idx_in = -1
            idx_out = -1
            for idx in range(len(tokens)):
                if tokens[idx] == token_in:
                    idx_in = idx
                if tokens[idx] == token_out:
                    idx_out = idx

            if idx_in == -1 or idx_out == -1:
                raise ContractError.TOKEN_INDEX_NOT_FOUND

            # Invoke StableSwap.swap(caller, i, j, dx, min_dy, deadline)
            out_amt = self.env.invoke_contract(pool, "swap", [
                self.env.current_contract(),
                U64(idx_in),
                U64(idx_out),
                amount_in,
                U128(1), # min checked globally at end of path
                deadline
            ])
            return out_amt

        else:
            raise ContractError.POOL_SWAP_FAILED

    # ------------------------------------------------------------------ #
    #  Internal Helpers
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
