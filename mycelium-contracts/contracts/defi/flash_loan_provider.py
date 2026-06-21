"""
Flash Loan Provider — Uncollateralized single-transaction flash loans.

Features:
  - Vault-style liquidity pool where LPs can deposit assets and earn flash loan fees
  - Shares-based accounting for pool deposits and withdrawals
  - Multi-token support with token validation
  - Callback execution pattern on the receiver address with custom data payload
  - Strict post-callback balance checks enforcing full repayment plus fees
  - Configurable flash loan fee basis points (admin-controlled)
  - Emergency kill switch (pause/unpause loans)
  - Reentrancy protection and security guards

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
    PAUSED = 5
    ZERO_AMOUNT = 6
    INVALID_TOKEN = 7
    INSUFFICIENT_POOL_BALANCE = 8
    FLASH_LOAN_NOT_REPAID = 9
    INVALID_FEE_BPS = 10
    ZERO_SHARES = 11
    INSUFFICIENT_SHARES = 12


# Constants
FEE_DENOMINATOR = U128(10000)
MAX_FEE_BPS = U64(200)  # 2.0% maximum flash loan fee


@contract
class FlashLoanProvider:
    """
    Uncollateralized single-transaction flash loan provider.
    Allows LPs to pool funds and earn fees from borrowers.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ------------------------------------------------------------------ #
    #  Initialization
    # ------------------------------------------------------------------ #

    @external
    def initialize(self, admin: Address, default_fee_bps: U64):
        """Initialise contract parameters and admin control."""
        admin.require_auth()

        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if default_fee_bps > MAX_FEE_BPS:
            raise ContractError.INVALID_FEE_BPS

        self.storage.set("admin", admin)
        self.storage.set("fee_bps", default_fee_bps)
        self.storage.set("paused", False)
        self.storage.set("reentrancy_locked", False)
        self.storage.set("supported_tokens", Vec())
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "fee_bps": default_fee_bps
        })

    # ------------------------------------------------------------------ #
    #  LP Liquidity Pool Functions
    # ------------------------------------------------------------------ #

    @external
    def deposit(self, provider: Address, token: Address, amount: U128) -> U128:
        """
        Deposit assets into the flash loan pool to earn yield.
        Mints LP shares corresponding to the provider's percentage.
        """
        provider.require_auth()
        self._require_initialized()
        self._require_not_locked()
        self._require_supported_token(token)

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        self._set_locked(True)

        total_shares = self.storage.get(f"total_shares:{token}", U128(0))
        # Get actual contract balance before deposit
        pool_balance = self._get_token_balance(token)

        shares_to_mint = U128(0)
        if total_shares == U128(0) or pool_balance == U128(0):
            shares_to_mint = amount
        else:
            shares_to_mint = (amount * total_shares) // pool_balance

        if shares_to_mint == U128(0):
            raise ContractError.ZERO_SHARES

        # Transfer tokens to the vault
        self.env.transfer(provider, self.env.current_contract(), token, amount)

        # Update shares in storage
        user_shares = self.storage.get(f"shares:{token}:{provider}", U128(0))
        self.storage.set(f"shares:{token}:{provider}", user_shares + shares_to_mint)
        self.storage.set(f"total_shares:{token}", total_shares + shares_to_mint)

        self._set_locked(False)

        self.env.emit_event("deposit", {
            "provider": provider,
            "token": token,
            "amount": amount,
            "shares_minted": shares_to_mint
        })
        return shares_to_mint

    @external
    def withdraw(self, provider: Address, token: Address, shares: U128) -> U128:
        """
        Withdraw assets from the flash loan pool by burning LP shares.
        """
        provider.require_auth()
        self._require_initialized()
        self._require_not_locked()
        self._require_supported_token(token)

        if shares == U128(0):
            raise ContractError.ZERO_AMOUNT

        self._set_locked(True)

        user_shares = self.storage.get(f"shares:{token}:{provider}", U128(0))
        if user_shares < shares:
            raise ContractError.INSUFFICIENT_SHARES

        total_shares = self.storage.get(f"total_shares:{token}", U128(0))
        pool_balance = self._get_token_balance(token)

        # Calculate withdrawal payout: shares * pool_balance / total_shares
        payout = (shares * pool_balance) // total_shares

        if payout == U128(0):
            raise ContractError.ZERO_AMOUNT

        # Burn shares
        self.storage.set(f"shares:{token}:{provider}", user_shares - shares)
        self.storage.set(f"total_shares:{token}", total_shares - shares)

        # Transfer tokens to provider
        self.env.transfer(self.env.current_contract(), provider, token, payout)

        self._set_locked(False)

        self.env.emit_event("withdraw", {
            "provider": provider,
            "token": token,
            "amount": payout,
            "shares_burned": shares
        })
        return payout

    # ------------------------------------------------------------------ #
    #  Flash Loan Execution
    # ------------------------------------------------------------------ #

    @external
    def flash_loan(
        self,
        receiver: Address,
        token: Address,
        amount: U128,
        callback_data: Bytes,
    ):
        """
        Execute an uncollateralized flash loan.
        Transfers amount, invokes receiver callback, and verifies repayment.
        """
        self._require_initialized()
        self._require_not_paused()
        self._require_not_locked()
        self._require_supported_token(token)

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        self._set_locked(True)

        balance_before = self._get_token_balance(token)
        if balance_before < amount:
            raise ContractError.INSUFFICIENT_POOL_BALANCE

        # Calculate loan fee
        fee_bps = U128(self.storage.get("fee_bps"))
        fee = (amount * fee_bps) // FEE_DENOMINATOR

        # Transfer tokens to borrower/receiver
        self.env.transfer(self.env.current_contract(), receiver, token, amount)

        # Invoke callback method on receiver address
        # receiver.on_flash_loan(self, token, amount, fee, callback_data)
        # We pass self address so receiver can refund to the correct address
        self.env.invoke_contract(receiver, "on_flash_loan", [
            self.env.current_contract(),
            token,
            amount,
            fee,
            callback_data
        ])

        # Verify repayment: balance_after must be >= balance_before + fee
        balance_after = self._get_token_balance(token)
        required_balance = balance_before + fee

        if balance_after < required_balance:
            raise ContractError.FLASH_LOAN_NOT_REPAID

        self._set_locked(False)

        self.env.emit_event("flash_loan_executed", {
            "receiver": receiver,
            "token": token,
            "amount": amount,
            "fee": fee
        })

    # ------------------------------------------------------------------ #
    #  Admin & Policy Controls
    # ------------------------------------------------------------------ #

    @external
    def add_token(self, caller: Address, token: Address):
        """Register a token as supported for pooling/borrowing. Admin only."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        supported = self.storage.get("supported_tokens")
        for i in range(len(supported)):
            if supported[i] == token:
                return # Already supported

        supported.append(token)
        self.storage.set("supported_tokens", supported)

        self.env.emit_event("token_supported", {
            "token": token
        })

    @external
    def remove_token(self, caller: Address, token: Address):
        """Deregister a supported token. Admin only."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        supported = self.storage.get("supported_tokens")
        new_supported = Vec()
        removed = False

        for i in range(len(supported)):
            if supported[i] == token:
                removed = True
                continue
            new_supported.append(supported[i])

        if removed:
            self.storage.set("supported_tokens", new_supported)
            self.env.emit_event("token_removed", {
                "token": token
            })

    @external
    def set_fee(self, caller: Address, new_fee_bps: U64):
        """Update flash loan fee. Admin only."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        if new_fee_bps > MAX_FEE_BPS:
            raise ContractError.INVALID_FEE_BPS

        old_fee = self.storage.get("fee_bps")
        self.storage.set("fee_bps", new_fee_bps)

        self.env.emit_event("fee_updated", {
            "old_fee": old_fee,
            "new_fee": new_fee_bps
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Toggle emergency paused state. Admin only."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        self.storage.set("paused", paused)

        self.env.emit_event("paused_toggled", {
            "paused": paused
        })

    # ------------------------------------------------------------------ #
    #  View Functions
    # ------------------------------------------------------------------ #

    @view
    def get_fee_bps(self) -> U64:
        """Get the current fee basis points."""
        return self.storage.get("fee_bps", U64(0))

    @view
    def get_pool_balance(self, token: Address) -> U128:
        """Get the total balance of token inside pool."""
        return self._get_token_balance(token)

    @view
    def get_user_shares(self, token: Address, user: Address) -> U128:
        """Get user LP shares for a token."""
        return self.storage.get(f"shares:{token}:{user}", U128(0))

    @view
    def get_total_shares(self, token: Address) -> U128:
        """Get total LP shares for a token."""
        return self.storage.get(f"total_shares:{token}", U128(0))

    @view
    def get_supported_tokens(self) -> Vec:
        """Return list of supported token Addresses."""
        return self.storage.get("supported_tokens", Vec())

    @view
    def is_paused(self) -> Bool:
        """Return paused status."""
        return self.storage.get("paused", False)

    # ------------------------------------------------------------------ #
    #  Internal Helpers
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

    def _require_not_locked(self):
        if self.storage.get("reentrancy_locked", False):
            raise ContractError.REENTRANCY_GUARD

    def _set_locked(self, locked: Bool):
        self.storage.set("reentrancy_locked", locked)

    def _require_supported_token(self, token: Address):
        supported = self.storage.get("supported_tokens")
        is_supported = False
        for i in range(len(supported)):
            if supported[i] == token:
                is_supported = True
                break
        if not is_supported:
            raise ContractError.INVALID_TOKEN

    def _get_token_balance(self, token: Address) -> U128:
        return self.env.token(token).balance(self.env.current_contract())
