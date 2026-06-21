"""
Collateralized Debt Position (CDP) — MakerDAO-style synthetic stablecoin minting engine.

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
    ILK_NOT_SUPPORTED = 4
    ILK_ALREADY_SUPPORTED = 5
    GLOBAL_CEILING_EXCEEDED = 6
    LOCAL_CEILING_EXCEEDED = 7
    UNDER_COLLATERALIZED = 8
    INSUFFICIENT_COLLATERAL = 9
    INSUFFICIENT_DEBT_REPAID = 10
    VAULT_NOT_FOUND = 11
    NOT_LIQUIDATABLE = 12
    SHUTDOWN_ACTIVE = 13
    SHUTDOWN_NOT_ACTIVE = 14
    ZERO_AMOUNT = 15
    ZERO_PRICE = 16
    OVERFLOW = 17
    INVALID_PARAMETER = 18


# ── Constants ────────────────────────────────────────────────────────────────

WAD = U128(1_000_000_000_000_000_000)  # 1e18 scale
SECONDS_PER_YEAR = U128(31_536_000)
BPS_DENOMINATOR = U128(10000)


@contract
class CollateralizedDebt:
    """
    MakerDAO-style CDP contract. Users can open vaults with supported collateral
    tokens (ilks) and mint a synthetic stablecoin (e.g., USDM) against it.
    The contract tracks stability fees (borrow interest), local and global debt
    ceilings, liquidations of under-collateralized vaults, and provides a multi-stage
    emergency shutdown system.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ── Administrative Functions ─────────────────────────────────────────────

    @external
    def initialize(self, admin: Address, stablecoin: Address, global_ceiling: U128):
        """
        Initializes the CDP core engine.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("stablecoin", stablecoin)
        self.storage.set("global_ceiling", global_ceiling)
        self.storage.set("global_debt", U128(0))
        self.storage.set("vault_count", U64(0))
        self.storage.set("shutdown", False)
        self.storage.set("shutdown_price_index", WAD)  # Redemption rate post-shutdown
        self.storage.set("initialized", True)
        self.storage.set("ilks_list", Vec())

        self.env.emit_event("initialized", {
            "admin": admin,
            "stablecoin": stablecoin,
            "global_ceiling": global_ceiling,
        })

    @external
    def add_ilk(
        self,
        caller: Address,
        ilk: Symbol,
        token: Address,
        decimals: U64,
        liq_ratio: U128,      # in bps, e.g. 15000 = 150%
        liq_penalty: U128,    # in bps, e.g. 1300 = 13%
        stability_fee: U128,  # annual rate in bps, e.g. 500 = 5%
        debt_ceiling: U128,   # local ceiling
    ):
        """
        Registers a new collateral type (ilk).
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self._require_no_shutdown()

        if liq_ratio < BPS_DENOMINATOR:
            raise ContractError.INVALID_PARAMETER

        # Check if already exists
        ilks = self.storage.get("ilks_list")
        for i in range(len(ilks)):
            if ilks[i] == ilk:
                raise ContractError.ILK_ALREADY_SUPPORTED

        config = {
            "token": token,
            "decimals": decimals,
            "liq_ratio": liq_ratio,
            "liq_penalty": liq_penalty,
            "stability_fee": stability_fee,
            "debt_ceiling": debt_ceiling,
        }
        self.storage.set(f"ilk_config:{ilk}", config)

        state = {
            "total_collateral": U128(0),
            "current_debt": U128(0),
            "debt_shares": U128(0),
            "debt_index": WAD,
            "last_update_time": self.env.ledger().timestamp(),
        }
        self.storage.set(f"ilk_state:{ilk}", state)

        # Mock initial price $1.00 (with 8 decimals)
        self.storage.set(f"ilk_price:{ilk}", U128(100_000_000))

        ilks.append(ilk)
        self.storage.set("ilks_list", ilks)

        self.env.emit_event("ilk_added", {
            "ilk": ilk,
            "token": token,
            "liq_ratio": liq_ratio,
            "debt_ceiling": debt_ceiling,
        })

    @external
    def set_price(self, caller: Address, ilk: Symbol, price_usd: U128):
        """
        Updates the price of a collateral type in USD (8 decimals).
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self._require_no_shutdown()

        self._get_ilk_config(ilk)  # Verification
        self.storage.set(f"ilk_price:{ilk}", price_usd)

        self.env.emit_event("price_updated", {
            "ilk": ilk,
            "price": price_usd,
        })

    @external
    def update_parameters(
        self,
        caller: Address,
        ilk: Symbol,
        liq_ratio: U128,
        liq_penalty: U128,
        stability_fee: U128,
        debt_ceiling: U128,
    ):
        """
        Updates risk parameters for a collateral type.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self._require_no_shutdown()

        config = self._get_ilk_config(ilk)
        self._accrue_stability_fee(ilk)

        config["liq_ratio"] = liq_ratio
        config["liq_penalty"] = liq_penalty
        config["stability_fee"] = stability_fee
        config["debt_ceiling"] = debt_ceiling

        self.storage.set(f"ilk_config:{ilk}", config)

        self.env.emit_event("parameters_updated", {
            "ilk": ilk,
            "liq_ratio": liq_ratio,
            "stability_fee": stability_fee,
            "debt_ceiling": debt_ceiling,
        })

    # ── Vault Interactions ───────────────────────────────────────────────────

    @external
    def open_vault(self, caller: Address, ilk: Symbol) -> U64:
        """
        Opens a new collateralized debt vault.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_no_shutdown()
        self._get_ilk_config(ilk)  # Ensure supported

        vault_id = self.storage.get("vault_count", U64(0))

        vault = {
            "owner": caller,
            "ilk": ilk,
            "collateral": U128(0),
            "debt_shares": U128(0),
        }
        self.storage.set(f"vault:{vault_id}", vault)
        self.storage.set("vault_count", vault_id + U64(1))

        # Track user's vaults
        user_vaults = self.storage.get(f"user_vaults:{caller}", Vec())
        user_vaults.append(vault_id)
        self.storage.set(f"user_vaults:{caller}", user_vaults)

        self.env.emit_event("vault_opened", {
            "vault_id": vault_id,
            "owner": caller,
            "ilk": ilk,
        })
        return vault_id

    @external
    def deposit(self, caller: Address, vault_id: U64, amount: U128):
        """
        Deposits collateral into the vault.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_no_shutdown()

        vault = self._get_vault(vault_id)
        config = self._get_ilk_config(vault["ilk"])
        state = self._get_ilk_state(vault["ilk"])

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        # Collect collateral tokens
        self.env.transfer(caller, self.env.current_contract(), config["token"], amount)

        # Scale amount to WAD (18 decimals)
        decimals = config["decimals"]
        amount_wad = amount * (10 ** (18 - decimals))

        # Update storage
        vault["collateral"] += amount_wad
        state["total_collateral"] += amount_wad

        self.storage.set(f"vault:{vault_id}", vault)
        self.storage.set(f"ilk_state:{vault['ilk']}", state)

        self.env.emit_event("collateral_deposited", {
            "vault_id": vault_id,
            "amount": amount,
            "new_collateral": vault["collateral"],
        })

    @external
    def withdraw(self, caller: Address, vault_id: U64, amount: U128):
        """
        Withdraws collateral. Fails if the vault falls below liquidation ratio.
        """
        caller.require_auth()
        self._require_initialized()

        vault = self._get_vault(vault_id)
        if vault["owner"] != caller:
            raise ContractError.UNAUTHORIZED

        config = self._get_ilk_config(vault["ilk"])
        self._accrue_stability_fee(vault["ilk"])
        state = self._get_ilk_state(vault["ilk"])

        # Scale amount to WAD
        decimals = config["decimals"]
        amount_wad = amount * (10 ** (18 - decimals))

        if amount_wad > vault["collateral"]:
            raise ContractError.INSUFFICIENT_COLLATERAL

        # Temporary deduct to check collateral ratio
        vault["collateral"] -= amount_wad
        state["total_collateral"] -= amount_wad

        # Evaluate vault safety (unless vault has no debt)
        if vault["debt_shares"] > U128(0):
            self._require_safe(vault, state["debt_index"], config["liq_ratio"])

        # Save updates
        self.storage.set(f"vault:{vault_id}", vault)
        self.storage.set(f"ilk_state:{vault['ilk']}", state)

        # Transfer tokens back
        self.env.transfer(self.env.current_contract(), caller, config["token"], amount)

        self.env.emit_event("collateral_withdrawn", {
            "vault_id": vault_id,
            "amount": amount,
            "new_collateral": vault["collateral"],
        })

    @external
    def draw_debt(self, caller: Address, vault_id: U64, amount_stablecoin: U128):
        """
        Mints and draws synthetic stablecoins from the vault.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_no_shutdown()

        vault = self._get_vault(vault_id)
        if vault["owner"] != caller:
            raise ContractError.UNAUTHORIZED

        config = self._get_ilk_config(vault["ilk"])
        self._accrue_stability_fee(vault["ilk"])
        state = self._get_ilk_state(vault["ilk"])

        if amount_stablecoin == U128(0):
            raise ContractError.ZERO_AMOUNT

        # Convert stablecoin draw amount to shares
        debt_index = state["debt_index"]
        shares = (amount_stablecoin * WAD) // debt_index

        # Check Ceilings
        global_debt = self.storage.get("global_debt")
        global_ceiling = self.storage.get("global_ceiling")
        new_global_debt = global_debt + amount_stablecoin
        if new_global_debt > global_ceiling:
            raise ContractError.GLOBAL_CEILING_EXCEEDED

        new_ilk_debt = state["current_debt"] + amount_stablecoin
        if new_ilk_debt > config["debt_ceiling"]:
            raise ContractError.LOCAL_CEILING_EXCEEDED

        # Update states
        vault["debt_shares"] += shares
        state["current_debt"] = new_ilk_debt
        state["debt_shares"] += shares

        # Verify new safety ratio
        self._require_safe(vault, debt_index, config["liq_ratio"])

        # Save states
        self.storage.set(f"vault:{vault_id}", vault)
        self.storage.set(f"ilk_state:{vault['ilk']}", state)
        self.storage.set("global_debt", new_global_debt)

        # Mint stablecoin to caller
        stablecoin = self.storage.get("stablecoin")
        self.env.mint(stablecoin, caller, amount_stablecoin)

        self.env.emit_event("debt_drawn", {
            "vault_id": vault_id,
            "amount": amount_stablecoin,
            "new_debt": (vault["debt_shares"] * debt_index) // WAD,
        })

    @external
    def repay_debt(self, caller: Address, vault_id: U64, amount_stablecoin: U128):
        """
        Repays stablecoin debt. Burns the stablecoin.
        """
        caller.require_auth()
        self._require_initialized()

        vault = self._get_vault(vault_id)
        config = self._get_ilk_config(vault["ilk"])
        self._accrue_stability_fee(vault["ilk"])
        state = self._get_ilk_state(vault["ilk"])

        if amount_stablecoin == U128(0):
            raise ContractError.ZERO_AMOUNT

        # Calculate current vault debt
        debt_index = state["debt_index"]
        vault_debt = (vault["debt_shares"] * debt_index) // WAD

        # Adjust repayment if it exceeds current debt
        repay_amount = amount_stablecoin
        if repay_amount > vault_debt:
            repay_amount = vault_debt

        shares_to_burn = (repay_amount * WAD) // debt_index
        if shares_to_burn > vault["debt_shares"]:
            shares_to_burn = vault["debt_shares"]

        # Update states
        vault["debt_shares"] -= shares_to_burn
        state["current_debt"] = state["current_debt"] - repay_amount if state["current_debt"] > repay_amount else U128(0)
        state["debt_shares"] -= shares_to_burn

        global_debt = self.storage.get("global_debt")
        self.storage.set("global_debt", global_debt - repay_amount if global_debt > repay_amount else U128(0))

        # Save states
        self.storage.set(f"vault:{vault_id}", vault)
        self.storage.set(f"ilk_state:{vault['ilk']}", state)

        # Burn stablecoin from payer
        stablecoin = self.storage.get("stablecoin")
        self.env.burn(stablecoin, caller, repay_amount)

        self.env.emit_event("debt_repaid", {
            "vault_id": vault_id,
            "amount": repay_amount,
            "remaining_debt": (vault["debt_shares"] * debt_index) // WAD,
        })

    # ── Liquidation ──────────────────────────────────────────────────────────

    @external
    def liquidate(self, caller: Address, vault_id: U64) -> U128:
        """
        Liquidates an unsafe vault.
        The liquidator repays the vault's debt and receives the collateral
        adjusted by the liquidation penalty.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_no_shutdown()

        vault = self._get_vault(vault_id)
        config = self._get_ilk_config(vault["ilk"])
        self._accrue_stability_fee(vault["ilk"])
        state = self._get_ilk_state(vault["ilk"])

        # Check if the vault is actually unsafe (under-collateralized)
        debt_index = state["debt_index"]
        vault_debt = (vault["debt_shares"] * debt_index) // WAD
        price = self.storage.get(f"ilk_price:{vault['ilk']}", U128(0))

        if price == U128(0):
            raise ContractError.ZERO_PRICE

        collateral_usd = (vault["collateral"] * price) // U128(100_000_000)

        # Safe if debt is zero or ratio >= liq_ratio
        if vault_debt > U128(0):
            ratio = (collateral_usd * BPS_DENOMINATOR) // vault_debt
            if ratio >= config["liq_ratio"]:
                raise ContractError.NOT_LIQUIDATABLE
        else:
            raise ContractError.NOT_LIQUIDATABLE

        # Calculate penalty: e.g. 13% penalty (11300 / 10000)
        # Liquidator pays debt, gets collateral value equal to debt + penalty
        debt_value_usd = vault_debt
        seizable_usd = (debt_value_usd * (BPS_DENOMINATOR + config["liq_penalty"])) // BPS_DENOMINATOR
        seizable_collateral = (seizable_usd * U128(100_000_000)) // price

        # Cap seizure to available collateral
        if seizable_collateral > vault["collateral"]:
            seizable_collateral = vault["collateral"]

        # Deduct from states
        vault["collateral"] -= seizable_collateral
        state["total_collateral"] -= seizable_collateral

        state["current_debt"] = state["current_debt"] - vault_debt if state["current_debt"] > vault_debt else U128(0)
        state["debt_shares"] -= vault["debt_shares"]

        global_debt = self.storage.get("global_debt")
        self.storage.set("global_debt", global_debt - vault_debt if global_debt > vault_debt else U128(0))

        # Clear vault debt
        vault["debt_shares"] = U128(0)

        # Save states
        self.storage.set(f"vault:{vault_id}", vault)
        self.storage.set(f"ilk_state:{vault['ilk']}", state)

        # Burn debt asset from liquidator
        stablecoin = self.storage.get("stablecoin")
        self.env.burn(stablecoin, caller, vault_debt)

        # Transfer collateral to liquidator
        decimals = config["decimals"]
        raw_seized = seizable_collateral // (10 ** (18 - decimals))
        self.env.transfer(self.env.current_contract(), caller, config["token"], raw_seized)

        self.env.emit_event("vault_liquidated", {
            "vault_id": vault_id,
            "liquidator": caller,
            "debt_burned": vault_debt,
            "collateral_seized": raw_seized,
        })
        return raw_seized

    # ── Emergency Shutdown (ES) ──────────────────────────────────────────────

    @external
    def emergency_shutdown(self, caller: Address):
        """
        Initiates emergency shutdown (cage).
        Locks all minting and sets final collateral prices.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self._require_no_shutdown()

        self.storage.set("shutdown", True)

        # Calculate redemption exchange rate: total collateral value / total debt
        # In multi-collateral system, it's simpler to allow users to redeem directly
        # based on individual ilk prices at shutdown. Let's record final prices.
        # This implementation freezes debt accumulation.
        self.env.emit_event("emergency_shutdown_triggered", {
            "block": self.env.ledger().timestamp()
        })

    @external
    def redeem_collateral(self, caller: Address, stablecoin_amount: U128, ilk: Symbol) -> U128:
        """
        Post-shutdown, users can burn stablecoins directly to redeem collateral
        at the fixed price index computed at shutdown.
        """
        self._require_initialized()
        if not self.storage.get("shutdown", False):
            raise ContractError.SHUTDOWN_NOT_ACTIVE

        if stablecoin_amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        config = self._get_ilk_config(ilk)
        price = self.storage.get(f"ilk_price:{ilk}", U128(0))
        if price == U128(0):
            raise ContractError.ZERO_PRICE

        # Value to redeem in USD (WAD scale)
        redeem_value_usd = stablecoin_amount
        collateral_wad = (redeem_value_usd * U128(100_000_000)) // price

        # Verify pool balance
        decimals = config["decimals"]
        raw_collateral = collateral_wad // (10 ** (18 - decimals))

        # Burn stablecoins
        stablecoin = self.storage.get("stablecoin")
        self.env.burn(stablecoin, caller, stablecoin_amount)

        # Transfer collateral to caller
        self.env.transfer(self.env.current_contract(), caller, config["token"], raw_collateral)

        self.env.emit_event("collateral_redeemed", {
            "redeemer": caller,
            "ilk": ilk,
            "stablecoin_burned": stablecoin_amount,
            "collateral_returned": raw_collateral,
        })
        return raw_collateral

    # ── View Functions ───────────────────────────────────────────────────────

    @view
    def get_vault_info(self, vault_id: U64) -> Map:
        """
        Returns details of a vault: owner, ilk, collateral, debt amount.
        """
        vault = self._get_vault(vault_id)
        state = self._get_ilk_state(vault["ilk"])
        
        vault_debt = (vault["debt_shares"] * state["debt_index"]) // WAD
        return {
            "owner": vault["owner"],
            "ilk": vault["ilk"],
            "collateral": vault["collateral"],
            "debt": vault_debt,
            "debt_shares": vault["debt_shares"],
        }

    @view
    def get_ilk_info(self, ilk: Symbol) -> Map:
        """
        Returns parameters and status of an ilk collateral type.
        """
        config = self._get_ilk_config(ilk)
        state = self._get_ilk_state(ilk)
        price = self.storage.get(f"ilk_price:{ilk}", U128(0))
        return {
            "config": config,
            "state": state,
            "price": price,
        }

    @view
    def get_global_state(self) -> Map:
        """
        Returns high-level stats of the CDP system.
        """
        return {
            "global_ceiling": self.storage.get("global_ceiling"),
            "global_debt": self.storage.get("global_debt"),
            "vault_count": self.storage.get("vault_count"),
            "shutdown": self.storage.get("shutdown"),
            "stablecoin": self.storage.get("stablecoin"),
        }

    # ── Internal Helpers ─────────────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_no_shutdown(self):
        if self.storage.get("shutdown", False):
            raise ContractError.SHUTDOWN_ACTIVE

    def _get_vault(self, vault_id: U64) -> Map:
        vault = self.storage.get(f"vault:{vault_id}", None)
        if vault is None:
            raise ContractError.VAULT_NOT_FOUND
        return vault

    def _get_ilk_config(self, ilk: Symbol) -> Map:
        config = self.storage.get(f"ilk_config:{ilk}", None)
        if config is None:
            raise ContractError.ILK_NOT_SUPPORTED
        return config

    def _get_ilk_state(self, ilk: Symbol) -> Map:
        state = self.storage.get(f"ilk_state:{ilk}", None)
        if state is None:
            raise ContractError.ILK_NOT_SUPPORTED
        return state

    def _accrue_stability_fee(self, ilk: Symbol):
        """
        Accrues stability fees (borrow interest) over time for an ilk.
        """
        state = self._get_ilk_state(ilk)
        config = self._get_ilk_config(ilk)
        now = self.env.ledger().timestamp()

        time_elapsed = U128(now - state["last_update_time"])
        if time_elapsed == U128(0):
            return

        fee_rate = config["stability_fee"]  # in bps
        # Convert bps rate to per-second WAD rate approximation
        # factor = fee_rate * time_elapsed / (SECONDS_PER_YEAR * 10000)
        interest_factor = (fee_rate * time_elapsed * WAD) // (SECONDS_PER_YEAR * BPS_DENOMINATOR)

        state["debt_index"] = (state["debt_index"] * (WAD + interest_factor)) // WAD
        
        # Calculate updated debt
        state["current_debt"] = (state["debt_shares"] * state["debt_index"]) // WAD
        state["last_update_time"] = now

        self.storage.set(f"ilk_state:{ilk}", state)

    def _require_safe(self, vault: Map, debt_index: U128, liq_ratio: U128):
        """
        Verifies if vault is safely collateralized.
        """
        price = self.storage.get(f"ilk_price:{vault['ilk']}", U128(0))
        if price == U128(0):
            raise ContractError.ZERO_PRICE

        collateral_usd = (vault["collateral"] * price) // U128(100_000_000)
        debt = (vault["debt_shares"] * debt_index) // WAD

        if debt > U128(0):
            ratio = (collateral_usd * BPS_DENOMINATOR) // debt
            if ratio < liq_ratio:
                raise ContractError.UNDER_COLLATERALIZED
