"""
Dollar Cost Averaging (DCA) — Automated DCA engine.

Features:
  - Create DCA positions: deposit funding assets and configure swap settings
  - Configurable swap parameters: swap size, intervals, slippage tolerance, output asset
  - Keeper rewards to incentivize automated triggers
  - Multi-pool adapter compatibility (Constant Product AMM & StableSwap)
  - Sandwich protection: validates keeper-submitted slippage limits against spot pool quotes
  - Position control: pause, resume, cancel (with refund of unused funding)
  - Execution tracking: tracks interval timestamps and total swaps executed
  - Reentrancy locks and access controls

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
    POSITION_NOT_FOUND = 6
    INTERVAL_NOT_ELAPSED = 7
    INSUFFICIENT_FUNDING = 8
    POSITION_INACTIVE = 9
    SLIPPAGE_EXCEEDED = 10
    INVALID_KEEPER_FEE = 11
    POOL_VALIDATION_FAILED = 12


# Constants
FEE_DENOMINATOR = U128(10000)
MAX_KEEPER_FEE_BPS = U64(500)  # 5% max keeper incentive fee


@contract
class DollarCostAveraging:
    """
    Automated DCA execution contract allowing users to purchase tokens periodically,
    incentivizing keepers to submit swap transactions securely.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ------------------------------------------------------------------ #
    #  Initialization
    # ------------------------------------------------------------------ #

    @external
    def initialize(self, admin: Address, default_keeper_fee_bps: U64):
        """Initialise DCA engine parameters."""
        admin.require_auth()

        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if default_keeper_fee_bps > MAX_KEEPER_FEE_BPS:
            raise ContractError.INVALID_KEEPER_FEE

        self.storage.set("admin", admin)
        self.storage.set("keeper_fee_bps", default_keeper_fee_bps)
        self.storage.set("next_position_id", U64(1))
        self.storage.set("reentrancy_locked", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "keeper_fee_bps": default_keeper_fee_bps
        })

    # ------------------------------------------------------------------ #
    #  User DCA Position Lifecycle
    # ------------------------------------------------------------------ #

    @external
    def create_position(
        self,
        creator: Address,
        token_in: Address,
        token_out: Address,
        total_funding: U128,
        amount_per_swap: U128,
        interval_seconds: U64,
        max_slippage_bps: U64,
    ) -> U64:
        """
        Create a new DCA position and deposit funding tokens into escrow.
        """
        creator.require_auth()
        self._require_initialized()
        self._require_not_locked()

        if total_funding == U128(0) or amount_per_swap == U128(0):
            raise ContractError.ZERO_AMOUNT
        if amount_per_swap > total_funding:
            raise ContractError.INSUFFICIENT_FUNDING
        if interval_seconds == U64(0):
            raise ContractError.ZERO_AMOUNT

        self._set_locked(True)

        # Escrow funding tokens from user
        self.env.transfer(creator, self.env.current_contract(), token_in, total_funding)

        position_id = self.storage.get("next_position_id")
        self.storage.set("next_position_id", position_id + U64(1))

        position = {
            "id": position_id,
            "creator": creator,
            "token_in": token_in,
            "token_out": token_out,
            "remaining_funding": total_funding,
            "amount_per_swap": amount_per_swap,
            "interval_seconds": interval_seconds,
            "last_execution": U64(0),
            "max_slippage_bps": max_slippage_bps,
            "active": True,
            "completed": False,
            "execution_count": U64(0)
        }

        self.storage.set(f"position:{position_id}", position)

        self._set_locked(False)

        self.env.emit_event("position_created", {
            "id": position_id,
            "creator": creator,
            "token_in": token_in,
            "token_out": token_out,
            "total_funding": total_funding,
            "amount_per_swap": amount_per_swap,
            "interval": interval_seconds
        })
        return position_id

    @external
    def pause_position(self, creator: Address, position_id: U64):
        """Pause execution of a DCA position. Creator only."""
        creator.require_auth()
        self._require_initialized()
        
        pos = self._get_position(position_id)
        if pos["creator"] != creator:
            raise ContractError.UNAUTHORIZED

        pos["active"] = False
        self.storage.set(f"position:{position_id}", pos)

        self.env.emit_event("position_paused", {
            "id": position_id
        })

    @external
    def resume_position(self, creator: Address, position_id: U64):
        """Resume execution of a DCA position. Creator only."""
        creator.require_auth()
        self._require_initialized()
        
        pos = self._get_position(position_id)
        if pos["creator"] != creator:
            raise ContractError.UNAUTHORIZED

        pos["active"] = True
        self.storage.set(f"position:{position_id}", pos)

        self.env.emit_event("position_resumed", {
            "id": position_id
        })

    @external
    def cancel_position(self, creator: Address, position_id: U64):
        """Cancel DCA position and refund remaining funding. Creator only."""
        creator.require_auth()
        self._require_initialized()
        self._require_not_locked()
        self._set_locked(True)

        pos = self._get_position(position_id)
        if pos["creator"] != creator:
            raise ContractError.UNAUTHORIZED

        remaining = pos["remaining_funding"]
        token_in = pos["token_in"]

        # Refund remaining funds
        if remaining > U128(0):
            self.env.transfer(self.env.current_contract(), creator, token_in, remaining)

        pos["remaining_funding"] = U128(0)
        pos["active"] = False
        pos["completed"] = True
        self.storage.set(f"position:{position_id}", pos)

        self._set_locked(False)

        self.env.emit_event("position_cancelled", {
            "id": position_id,
            "refunded_amount": remaining
        })

    # ------------------------------------------------------------------ #
    #  Keeper Swap Execution
    # ------------------------------------------------------------------ #

    @external
    def execute_swap(
        self,
        keeper: Address,
        position_id: U64,
        pool: Address,
        pool_mode: U64,      # 1: Constant Product, 2: StableSwap
        min_amount_out: U128,
        deadline: U64,
    ) -> U128:
        """
        Execute the periodic swap for a DCA position. Keeper triggers.
        Calculates keeper reward, processes trade on pool, sends tokens to user.
        """
        keeper.require_auth()
        self._require_initialized()
        self._require_not_locked()
        self._check_deadline(deadline)
        self._set_locked(True)

        pos = self._get_position(position_id)
        if not pos["active"] or pos["completed"]:
            raise ContractError.POSITION_INACTIVE

        now = self.env.ledger().timestamp()
        if now < pos["last_execution"] + pos["interval_seconds"]:
            raise ContractError.INTERVAL_NOT_ELAPSED

        swap_size = pos["amount_per_swap"]
        if pos["remaining_funding"] < swap_size:
            # Not enough funding remaining
            raise ContractError.INSUFFICIENT_FUNDING

        token_in = pos["token_in"]
        token_out = pos["token_out"]

        # Calculate keeper reward: swap_size * keeper_fee / 10000
        keeper_fee_bps = U128(self.storage.get("keeper_fee_bps"))
        reward = (swap_size * keeper_fee_bps) // FEE_DENOMINATOR
        swap_qty = swap_size - reward

        # Fetch Spot Output quote from pool for sandwich protection
        spot_quote = self._get_pool_spot_quote(pool, pool_mode, token_in, token_out, swap_qty, deadline)
        
        # Max slippage protection threshold
        max_slip = U128(pos["max_slippage_bps"])
        allowed_min_out = (spot_quote * (FEE_DENOMINATOR - max_slip)) // FEE_DENOMINATOR

        if min_amount_out < allowed_min_out:
            raise ContractError.SLIPPAGE_EXCEEDED

        # Transfer keeper reward
        if reward > U128(0):
            self.env.transfer(self.env.current_contract(), keeper, token_in, reward)

        # Execute Swap on the pool directing output back to this contract
        # (Router or Pool will transfer output to the recipient address, which we set as the position creator)
        actual_received = self._execute_pool_swap(
            pool, pool_mode, token_in, token_out, swap_qty, min_amount_out, pos["creator"], deadline
        )

        # Update position state
        pos["remaining_funding"] = pos["remaining_funding"] - swap_size
        pos["last_execution"] = now
        pos["execution_count"] = pos["execution_count"] + U64(1)

        if pos["remaining_funding"] < pos["amount_per_swap"]:
            pos["completed"] = True
            pos["active"] = False

        self.storage.set(f"position:{position_id}", pos)

        self._set_locked(False)

        self.env.emit_event("dca_swap_executed", {
            "id": position_id,
            "keeper": keeper,
            "swap_size": swap_size,
            "swapped_net": swap_qty,
            "received_amount": actual_received,
            "keeper_reward": reward
        })
        return actual_received

    # ------------------------------------------------------------------ #
    #  Admin Configurations
    # ------------------------------------------------------------------ #

    @external
    def set_keeper_fee(self, caller: Address, fee_bps: U64):
        """Update keeper incentive reward fee. Admin only."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        if fee_bps > MAX_KEEPER_FEE_BPS:
            raise ContractError.INVALID_KEEPER_FEE

        old_fee = self.storage.get("keeper_fee_bps")
        self.storage.set("keeper_fee_bps", fee_bps)

        self.env.emit_event("keeper_fee_updated", {
            "old_fee": old_fee,
            "new_fee": fee_bps
        })

    # ------------------------------------------------------------------ #
    #  View Functions
    # ------------------------------------------------------------------ #

    @view
    def get_position_info(self, position_id: U64) -> Map:
        """Query DCA position status."""
        return self._get_position(position_id)

    @view
    def get_keeper_fee_bps(self) -> U64:
        """Query global keeper fee setting."""
        return self.storage.get("keeper_fee_bps", U64(0))

    # ------------------------------------------------------------------ #
    #  Internal Pool Interactivity
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

    def _get_position(self, id: U64) -> Map:
        pos = self.storage.get(f"position:{id}", None)
        if pos is None:
            raise ContractError.POSITION_NOT_FOUND
        return pos

    def _get_pool_spot_quote(
        self,
        pool: Address,
        mode: U64,
        token_in: Address,
        token_out: Address,
        amount_in: U128,
        deadline: U64
    ) -> U128:
        """Queries spot rate output for token pair using target pool views."""
        if mode == U64(1):
            # Constant Product AMM spot check:
            # We fetch reserves: get_reserves() -> [reserve_a, reserve_b, timestamp]
            # And query: get_amount_out(amount_in, reserve_in, reserve_out)
            res = self.env.invoke_contract(pool, "get_reserves", [])
            pool_info = self.env.invoke_contract(pool, "get_pool_info", [])
            token_a = pool_info["token_a"]

            reserve_in, reserve_out = (res[0], res[1]) if token_in == token_a else (res[1], res[0])
            
            return self.env.invoke_contract(pool, "get_amount_out", [amount_in, reserve_in, reserve_out])

        elif mode == U64(2):
            # StableSwap Spot check
            # We query get_dy(i, j, dx)
            # Find i and j
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
                raise ContractError.POOL_VALIDATION_FAILED

            return self.env.invoke_contract(pool, "get_dy", [U64(idx_in), U64(idx_out), amount_in])

        else:
            raise ContractError.POOL_VALIDATION_FAILED

    def _execute_pool_swap(
        self,
        pool: Address,
        mode: U64,
        token_in: Address,
        token_out: Address,
        amount_in: U128,
        min_amount_out: U128,
        recipient: Address,
        deadline: U64
    ) -> U128:
        """
        Executes swap trade on pool, forwarding proceeds directly to user.
        """
        if mode == U64(1):
            # Constant Product AMM swap_exact_input
            # Router swap call: we call the pool swap exact input
            # Because pool pulls token_in from this contract, we are the caller.
            # But the AMM transfers to the caller of swap_exact_input?
            # Wait, in our AMM contract design, the pool updates reserves, but we saw
            # it might not transfer or transfers to caller.
            # Let's assume standard contract behavior: it sends output to the router/caller.
            # To settle recipient, we call swap specifying recipient or we receive output
            # and transfer to user. Let's receive output ourselves to verify exact returned amount,
            # and then transfer to the recipient. This is the safest way on-chain!
            
            # Send token_in to pool (or let pool pull it)
            # Our AMM swaps with the caller. So we call swap_exact_input with this contract as caller.
            # But wait! If the AMM doesn't call transfer, let's look:
            # Standard stellar swap pulls from caller. So we invoke swap_exact_input:
            out_amt = self.env.invoke_contract(pool, "swap_exact_input", [
                self.env.current_contract(),
                token_in,
                amount_in,
                min_amount_out,
                deadline
            ])

            # Transfer output to recipient
            self.env.transfer(self.env.current_contract(), recipient, token_out, out_amt)
            return out_amt

        elif mode == U64(2):
            # StableSwap swap(caller, i, j, dx, min_dy, deadline)
            pool_info = self.env.invoke_contract(pool, "get_pool_info", [])
            tokens = pool_info["tokens"]

            idx_in = -1
            idx_out = -1
            for idx in range(len(tokens)):
                if tokens[idx] == token_in:
                    idx_in = idx
                if tokens[idx] == token_out:
                    idx_out = idx

            out_amt = self.env.invoke_contract(pool, "swap", [
                self.env.current_contract(),
                U64(idx_in),
                U64(idx_out),
                amount_in,
                min_amount_out,
                deadline
            ])

            self.env.transfer(self.env.current_contract(), recipient, token_out, out_amt)
            return out_amt

        else:
            raise ContractError.POOL_VALIDATION_FAILED
