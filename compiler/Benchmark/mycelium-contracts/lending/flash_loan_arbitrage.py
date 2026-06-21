"""
Flash Loan Arbitrage — Execution bot that performs risk-free arbitrage using flash loans.

Mycelium Smart Contract for Stellar
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


# ── Error Codes ──────────────────────────────────────────────────────────────

class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    UNPROFITABLE_ARBITRAGE = 4
    INVALID_DEX = 5
    SLIPPAGE_EXCEEDED = 6
    CALLBACK_ONLY = 7
    INVALID_ASSET = 8
    ZERO_AMOUNT = 9
    OVERFLOW = 10


@contract
class FlashLoanArbitrage:
    """
    Arbitrage consumer contract that integrates with a Mycelium Lending Pool.
    It takes a flash loan, swaps the borrowed asset on DEX A for an intermediate asset,
    swaps it back on DEX B, verifies if the arbitrage yielded a profit after
    covering the loan fee, repays the pool, and sends the profit to the initiator.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """
        Initializes the contract. Sets the administrator.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("initialized", True)
        self.storage.set("approved_dexes", Vec())

        self.env.emit_event("initialized", {"admin": admin})

    @external
    def set_dex_approval(self, caller: Address, dex: Address, approved: Bool):
        """
        Approves or revokes a DEX address for trading. Admin-only.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        dexes = self.storage.get("approved_dexes")
        found = False
        for i in range(len(dexes)):
            if dexes[i] == dex:
                found = True
                if not approved:
                    dexes.remove(i)
                break

        if approved and not found:
            dexes.append(dex)

        self.storage.set("approved_dexes", dexes)
        self.env.emit_event("dex_status_changed", {"dex": dex, "approved": approved})

    @external
    def execute_arbitrage(
        self,
        caller: Address,
        pool: Address,
        asset: Address,
        amount: U128,
        intermediate_asset: Address,
        dex_a: Address,
        dex_b: Address,
        min_profit: U128,
    ) -> U128:
        """
        Triggers the flash loan arbitrage.
        Requests `amount` of `asset` from the flash loan `pool`.
        """
        caller.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        # Verify DEXes are approved
        self._require_approved_dex(dex_a)
        self._require_approved_dex(dex_b)

        # Build callback data to verify state on reentry
        callback_data = {
            "initiator": caller,
            "dex_a": dex_a,
            "dex_b": dex_b,
            "asset_intermediate": intermediate_asset,
            "min_profit": min_profit,
            "lending_pool": pool,
        }

        self.env.emit_event("arbitrage_started", {
            "initiator": caller,
            "asset": asset,
            "amount": amount,
            "dex_a": dex_a,
            "dex_b": dex_b,
        })

        # Call flash_loan on lending pool.
        # Signature: flash_loan(receiver: Address, asset: Address, amount: U128, callback_data: Map)
        # Note: Lending pool transfers the amount to this contract and then invokes flash_loan_callback.
        self.env.call(
            pool,
            "flash_loan",
            [self.env.current_contract(), asset, amount, callback_data]
        )

        # Retrieve profit recorded during callback
        profit = self.storage.get("last_arbitrage_profit", U128(0))
        self.storage.set("last_arbitrage_profit", U128(0))  # Clear slot

        return profit

    @external
    def flash_loan_callback(
        self,
        pool_caller: Address,
        asset: Address,
        amount: U128,
        fee: U128,
        callback_data: Map,
    ):
        """
        Callback function executed by the lending pool during flash loan.
        Performs the two-way swap, verifies profitability, repays the pool.
        """
        # The pool contract must authorize this callback call
        pool_caller.require_auth()
        self._require_initialized()

        # Security Check: callback_data must contain the lending pool and match the caller
        expected_pool = callback_data["lending_pool"]
        if pool_caller != expected_pool:
            raise ContractError.CALLBACK_ONLY

        initiator = callback_data["initiator"]
        dex_a = callback_data["dex_a"]
        dex_b = callback_data["dex_b"]
        intermediate_asset = callback_data["asset_intermediate"]
        min_profit = callback_data["min_profit"]

        # DEX swaps execution
        # 1. Swap borrowed asset on DEX A for intermediate token
        # Signature: swap_exact_input(caller, token_in, amount_in, min_amount_out, deadline)
        deadline = self.env.ledger().timestamp() + U64(300)  # 5 minutes deadline
        
        intermediate_received = self.env.call(
            dex_a,
            "swap_exact_input",
            [self.env.current_contract(), asset, amount, U128(1), deadline]
        )

        if intermediate_received == U128(0):
            raise ContractError.SLIPPAGE_EXCEEDED

        # 2. Swap intermediate token back to original asset on DEX B
        final_received = self.env.call(
            dex_b,
            "swap_exact_input",
            [self.env.current_contract(), intermediate_asset, intermediate_received, U128(1), deadline]
        )

        # Profitability validation
        required_repayment = amount + fee
        if final_received < required_repayment:
            raise ContractError.UNPROFITABLE_ARBITRAGE

        net_profit = final_received - required_repayment
        if net_profit < min_profit:
            raise ContractError.UNPROFITABLE_ARBITRAGE

        # Repay flash loan pool
        # Pool expects required_repayment of asset to be returned
        self.env.transfer(self.env.current_contract(), expected_pool, asset, required_repayment)

        # Transfer net profit to arbitrage initiator
        if net_profit > U128(0):
            self.env.transfer(self.env.current_contract(), initiator, asset, net_profit)

        # Store profit result temporarily for the initiator's call
        self.storage.set("last_arbitrage_profit", net_profit)

        self.env.emit_event("arbitrage_completed", {
            "initiator": initiator,
            "repaid": required_repayment,
            "profit": net_profit,
        })

    @external
    def emergency_recover_tokens(self, caller: Address, token: Address, to: Address, amount: U128):
        """
        Emergency token recovery for stuck tokens in contract. Admin-only.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        self.env.transfer(self.env.current_contract(), to, token, amount)
        self.env.emit_event("emergency_recovery", {"token": token, "to": to, "amount": amount})

    # ── View Functions ───────────────────────────────────────────────────────

    @view
    def is_dex_approved(self, dex: Address) -> Bool:
        """
        Checks if a DEX is approved for arbitrage.
        """
        dexes = self.storage.get("approved_dexes", Vec())
        for i in range(len(dexes)):
            if dexes[i] == dex:
                return True
        return False

    @view
    def get_contract_info(self) -> Map:
        """
        Returns contract admin and approved DEXes.
        """
        return {
            "admin": self.storage.get("admin"),
            "approved_dexes": self.storage.get("approved_dexes"),
        }

    # ── Internal Helpers ─────────────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_approved_dex(self, dex: Address):
        if not self.is_dex_approved(dex):
            raise ContractError.INVALID_DEX
