"""
Synthetic Asset Vault — Collateral backing, minting, stability fees, and liquidations.

Mycelium Smart Contract for Stellar. Allows users to lock collateral assets to mint
pegged synthetic assets. Enforces collateralization ratios, accrues interest stability fees,
and permits public liquidations with bonuses if a vault's backing falls below the safety threshold.
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
    INSUFFICIENT_COLLATERAL = 5
    UNDER_COLLATERALIZED = 6
    VAULT_NOT_FOUND = 7
    NOT_LIQUIDATABLE = 8
    ORACLE_READ_FAILED = 9
    INVALID_AMOUNT = 10
    REPAY_EXCEEDS_DEBT = 11

@contract
class SyntheticAsset:
    """
    Synthetic asset CDP (Collateralized Debt Position) vault manager.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        oracle: Address,
        collateral_token: Address,
        synthetic_token: Address,     # The token being minted (e.g. sUSD)
        collateral_market: Symbol,     # Oracle market symbol for collateral (e.g. XLM)
        synthetic_market: Symbol,      # Oracle market symbol for synthetic (e.g. USD)
        liquidation_ratio_bps: U64,    # e.g. 15000 for 150%
        liquidation_penalty_bps: U64,  # e.g. 1000 for 10% bonus to liquidator
        stability_fee_bps: U64         # e.g. 500 for 5% annual stability fee
    ):
        """Initialize CDP vault configurations, tokens, and interest fee index."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("oracle", oracle)
        self.storage.set("collateral_token", collateral_token)
        self.storage.set("synthetic_token", synthetic_token)
        self.storage.set("collateral_market", collateral_market)
        self.storage.set("synthetic_market", synthetic_market)
        self.storage.set("liquidation_ratio_bps", liquidation_ratio_bps)
        self.storage.set("liquidation_penalty_bps", liquidation_penalty_bps)
        self.storage.set("stability_fee_bps", stability_fee_bps)

        # Initialize global stability fee index. Multiplied by 10^12 for precision.
        self.storage.set("global_stability_index", U128(1_000_000_000_000))
        self.storage.set("last_fee_update_time", self._get_now())

        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "collateral": collateral_token,
            "synthetic": synthetic_token,
            "liquidation_ratio": liquidation_ratio_bps
        })

    @external
    def deposit_collateral(self, caller: Address, amount: U128):
        """
        Deposit collateral token into caller's vault.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if amount == U128(0):
            raise ContractError.INVALID_AMOUNT

        # Accrue interest before changing balances
        self._accrue_stability_fees()

        # Transfer collateral to contract
        token = self.storage.get("collateral_token")
        contract_addr = self.env.current_contract_address()
        self.env.call(token, "transfer", caller, contract_addr, amount)

        # Update vault balance
        col_bal = self.storage.get(f"vault_collateral_{caller}", U128(0))
        self.storage.set(f"vault_collateral_{caller}", col_bal + amount)

        # Sync user's debt checkpoint
        self._sync_vault_debt_checkpoint(caller)

        self.env.emit_event("collateral_deposited", {
            "user": caller,
            "amount": amount,
            "total_collateral": col_bal + amount
        })

    @external
    def withdraw_collateral(self, caller: Address, amount: U128):
        """
        Withdraw collateral from user's vault. Checks collateralization ratio safety.
        """
        caller.require_auth()
        self._require_initialized()

        col_bal = self.storage.get(f"vault_collateral_{caller}", U128(0))
        if amount > col_bal:
            raise ContractError.INSUFFICIENT_COLLATERAL

        self._accrue_stability_fees()

        # Update collateral balance temporarily
        new_col_bal = col_bal - amount
        self.storage.set(f"vault_collateral_{caller}", new_col_bal)

        # Check collateralization safety (C-Ratio)
        self._require_vault_healthy(caller)

        # Sync user's debt checkpoint
        self._sync_vault_debt_checkpoint(caller)

        # Transfer collateral back
        token = self.storage.get("collateral_token")
        contract_addr = self.env.current_contract_address()
        self.env.call(token, "transfer", contract_addr, caller, amount)

        self.env.emit_event("collateral_withdrawn", {
            "user": caller,
            "amount": amount,
            "remaining_collateral": new_col_bal
        })

    @external
    def mint_synthetic(self, caller: Address, amount: U128):
        """
        Mint synthetic tokens against locked collateral.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if amount == U128(0):
            raise ContractError.INVALID_AMOUNT

        self._accrue_stability_fees()

        # Retrieve current debt and apply interest index
        user_debt = self._get_vault_debt(caller)
        new_debt = user_debt + amount

        # Save debt principal (debt amount scaled back to stability index)
        global_idx = self.storage.get("global_stability_index", U128(1_000_000_000_000))
        principal_debt = (new_debt * U128(1_000_000_000_000)) / global_idx
        self.storage.set(f"vault_debt_principal_{caller}", principal_debt)
        self.storage.set(f"vault_debt_checkpoint_{caller}", global_idx)

        # Validate C-Ratio safety
        self._require_vault_healthy(caller)

        # Call mint on synthetic token contract
        # CDP manager contract must have mint permissions on the synthetic token
        synthetic_token = self.storage.get("synthetic_token")
        self.env.call(synthetic_token, "mint", caller, amount)

        self.env.emit_event("synthetic_minted", {
            "user": caller,
            "amount": amount,
            "total_debt": new_debt
        })

    @external
    def burn_synthetic(self, caller: Address, amount: U128):
        """
        Burn synthetic tokens to repay debt.
        """
        caller.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.INVALID_AMOUNT

        self._accrue_stability_fees()

        user_debt = self._get_vault_debt(caller)
        if amount > user_debt:
            raise ContractError.REPAY_EXCEEDS_DEBT

        new_debt = user_debt - amount

        # Save updated debt principal
        global_idx = self.storage.get("global_stability_index", U128(1_000_000_000_000))
        principal_debt = (new_debt * U128(1_000_000_000_000)) / global_idx
        self.storage.set(f"vault_debt_principal_{caller}", principal_debt)
        self.storage.set(f"vault_debt_checkpoint_{caller}", global_idx)

        # Burn tokens from caller address (requires CDP manager contract authorization)
        synthetic_token = self.storage.get("synthetic_token")
        self.env.call(synthetic_token, "burn", caller, amount)

        self.env.emit_event("synthetic_burned", {
            "user": caller,
            "repaid_amount": amount,
            "remaining_debt": new_debt
        })

    @external
    def liquidate_vault(self, caller: Address, vault_owner: Address, synthetic_to_repay: U128):
        """
        Liquidate an under-collateralized vault. The liquidator repays synthetic debt
        in exchange for proportional collateral plus a penalty/bonus payout.
        """
        caller.require_auth()
        self._require_initialized()

        self._accrue_stability_fees()

        # Check if the vault is actually under-collateralized (liquidatable)
        is_liquidatable, total_collateral, total_debt = self._check_vault_liquidation_status(vault_owner)
        if not is_liquidatable:
            raise ContractError.NOT_LIQUIDATABLE

        if synthetic_to_repay == U128(0) or synthetic_to_repay > total_debt:
            raise ContractError.REPAY_EXCEEDS_DEBT

        # Fetch oracle prices
        col_mkt = self.storage.get("collateral_market")
        syn_mkt = self.storage.get("synthetic_market")
        col_price = self._get_oracle_price(col_mkt)
        syn_price = self._get_oracle_price(syn_mkt)

        # Calculate proportional collateral reward including penalty/bonus
        # collateral_reward = (synthetic_repaid * syn_price * (1 + bonus_penalty)) / col_price
        penalty_bps = self.storage.get("liquidation_penalty_bps", U64(0))
        repay_val = synthetic_to_repay * syn_price
        reward_val = (repay_val * (U128(10000) + U128(penalty_bps))) / U128(10000)
        collateral_reward = reward_val / col_price

        # Limit reward payout to actual vault collateral size
        if collateral_reward > total_collateral:
            collateral_reward = total_collateral

        # Update vault balance
        new_col = total_collateral - collateral_reward
        self.storage.set(f"vault_collateral_{vault_owner}", new_col)

        # Repay debt
        new_debt = total_debt - synthetic_to_repay
        global_idx = self.storage.get("global_stability_index", U128(1_000_000_000_000))
        principal_debt = (new_debt * U128(1_000_000_000_000)) / global_idx
        self.storage.set(f"vault_debt_principal_{vault_owner}", principal_debt)
        self.storage.set(f"vault_debt_checkpoint_{vault_owner}", global_idx)

        # Burn synthetic token from liquidator
        synthetic_token = self.storage.get("synthetic_token")
        self.env.call(synthetic_token, "burn", caller, synthetic_to_repay)

        # Disburse collateral reward to liquidator
        col_token = self.storage.get("collateral_token")
        contract_addr = self.env.current_contract_address()
        self.env.call(col_token, "transfer", contract_addr, caller, collateral_reward)

        self.env.emit_event("vault_liquidated", {
            "vault_owner": vault_owner,
            "liquidator": caller,
            "repaid_debt": synthetic_to_repay,
            "collateral_claimed": collateral_reward,
            "remaining_collateral": new_col,
            "remaining_debt": new_debt
        })

    @external
    def set_stability_fee(self, caller: Address, stability_fee_bps: U64):
        """Modify the annual stability fee rate (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        self._accrue_stability_fees()
        self.storage.set("stability_fee_bps", stability_fee_bps)
        self.env.emit_event("stability_fee_updated", {"fee_bps": stability_fee_bps})

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause CDP operations (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_vault_info(self, owner: Address) -> Map:
        """Query vault collateral, current debt (accrued), and C-Ratio."""
        res = Map(self.env)
        collateral = self.storage.get(f"vault_collateral_{owner}", U128(0))
        debt = self._get_vault_debt(owner)

        col_mkt = self.storage.get("collateral_market")
        syn_mkt = self.storage.get("synthetic_market")
        col_price = self._get_oracle_price(col_mkt)
        syn_price = self._get_oracle_price(syn_mkt)

        col_val = collateral * col_price
        debt_val = debt * syn_price

        c_ratio_bps = U128(0)
        if debt_val > U128(0):
            c_ratio_bps = (col_val * U128(10000)) / debt_val

        res.set("collateral", collateral)
        res.set("debt", debt)
        res.set("c_ratio_bps", c_ratio_bps)
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

    def _get_oracle_price(self, market: Symbol) -> U128:
        oracle = self.storage.get("oracle")
        try:
            return self.env.call(oracle, "get_price", market)
        except Exception:
            raise ContractError.ORACLE_READ_FAILED

    def _accrue_stability_fees(self):
        """Accrue interest stability fee globally using elapsed time."""
        now = self._get_now()
        last_time = self.storage.get("last_fee_update_time", U64(0))
        if now <= last_time:
            return

        elapsed = now - last_time
        fee_bps = self.storage.get("stability_fee_bps", U64(0))
        global_idx = self.storage.get("global_stability_index", U128(1_000_000_000_000))

        # Interest = fee_bps * elapsed / (31536000 * 10000)
        # s_index = s_index * (1 + interest)
        yearly_denom = U128(31_536_000 * 10000)
        interest = (U128(fee_bps) * U128(int(elapsed)) * U128(1_000_000_000_000)) / yearly_denom
        new_idx = global_idx + (global_idx * interest) / U128(1_000_000_000_000)

        self.storage.set("global_stability_index", new_idx)
        self.storage.set("last_fee_update_time", now)

    def _get_vault_debt(self, owner: Address) -> U128:
        """Compute the accrued debt of a user vault based on the stability index."""
        principal = self.storage.get(f"vault_debt_principal_{owner}", U128(0))
        if principal == U128(0):
            return U128(0)

        global_idx = self.storage.get("global_stability_index", U128(1_000_000_000_000))
        
        # debt = principal * global_index / 10^12
        return (principal * global_idx) / U128(1_000_000_000_000)

    def _sync_vault_debt_checkpoint(self, owner: Address):
        global_idx = self.storage.get("global_stability_index", U128(1_000_000_000_000))
        self.storage.set(f"vault_debt_checkpoint_{owner}", global_idx)

    def _require_vault_healthy(self, owner: Address):
        """Revert transaction if C-Ratio falls below the liquidation safety threshold."""
        collateral = self.storage.get(f"vault_collateral_{owner}", U128(0))
        debt = self._get_vault_debt(owner)

        if debt == U128(0):
            return

        col_mkt = self.storage.get("collateral_market")
        syn_mkt = self.storage.get("synthetic_market")
        col_price = self._get_oracle_price(col_mkt)
        syn_price = self._get_oracle_price(syn_mkt)

        col_val = collateral * col_price
        debt_val = debt * syn_price

        # C-Ratio = col_val / debt_val. Safety Check: C-Ratio >= liquidation_ratio
        liq_ratio = self.storage.get("liquidation_ratio_bps", U64(0))
        required_col_val = (debt_val * U128(liq_ratio)) / U128(10000)

        if col_val < required_col_val:
            raise ContractError.UNDER_COLLATERALIZED

    def _check_vault_liquidation_status(self, owner: Address) -> (Bool, U128, U128):
        collateral = self.storage.get(f"vault_collateral_{owner}", U128(0))
        debt = self._get_vault_debt(owner)

        if debt == U128(0):
            return False, collateral, debt

        col_mkt = self.storage.get("collateral_market")
        syn_mkt = self.storage.get("synthetic_market")
        col_price = self._get_oracle_price(col_mkt)
        syn_price = self._get_oracle_price(syn_mkt)

        col_val = collateral * col_price
        debt_val = debt * syn_price

        liq_ratio = self.storage.get("liquidation_ratio_bps", U64(0))
        required_col_val = (debt_val * U128(liq_ratio)) / U128(10000)

        return (col_val < required_col_val), collateral, debt
