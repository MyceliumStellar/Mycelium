"""
Credit Delegation — Peer credit line approval borrowing against delegator's collateral.

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
    INSUFFICIENT_COLLATERAL = 4
    LIMIT_EXCEEDED = 5
    CREDIT_LINE_NOT_FOUND = 6
    CREDIT_LINE_SUSPENDED = 7
    HEALTH_FACTOR_TOO_LOW = 8
    ZERO_AMOUNT = 9
    OVERFLOW = 10
    ASSET_NOT_SUPPORTED = 11
    GRACE_PERIOD_ACTIVE = 12
    NO_DEBT = 13
    INVALID_PARAMETER = 14


# ── Constants ────────────────────────────────────────────────────────────────

WAD = U128(1_000_000_000_000_000_000)  # 1e18 scale
SECONDS_PER_YEAR = U128(31_536_000)
BPS_DENOMINATOR = U128(10000)


@contract
class CreditDelegation:
    """
    Credit Delegation engine.
    Delegators deposit collateral in the contract and approve credit lines (limits)
    for delegatees to borrow a debt asset against that collateral.
    Delegators can charge an interest premium on top of the base interest rate.
    If a delegatee defaults on payment or the delegator's health factor drops,
    suspension or liquidation cascades are triggered.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ── Admin Operations ─────────────────────────────────────────────────────

    @external
    def initialize(self, admin: Address, base_borrow_rate_bps: U128):
        """
        Initializes the credit delegation contract.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("base_borrow_rate", base_borrow_rate_bps)
        self.storage.set("collateral_assets", Vec())
        self.storage.set("debt_assets", Vec())
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "base_borrow_rate": base_borrow_rate_bps,
        })

    @external
    def add_supported_assets(
        self,
        caller: Address,
        asset: Address,
        decimals: U64,
        is_collateral: Bool,
        ltv_bps: U128,
        liq_threshold_bps: U128,
    ):
        """
        Adds configuration for collateral or debt assets. Admin-only.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        if ltv_bps >= liq_threshold_bps or liq_threshold_bps > BPS_DENOMINATOR:
            raise ContractError.INVALID_PARAMETER

        config = {
            "decimals": decimals,
            "is_collateral": is_collateral,
            "ltv": ltv_bps,
            "liq_threshold": liq_threshold_bps,
        }
        self.storage.set(f"asset_config:{asset}", config)

        if is_collateral:
            col_list = self.storage.get("collateral_assets")
            col_list.append(asset)
            self.storage.set("collateral_assets", col_list)
        else:
            debt_list = self.storage.get("debt_assets")
            debt_list.append(asset)
            self.storage.set("debt_assets", debt_list)

        # Set default mock price to $1.00 (8 decimals)
        self.storage.set(f"asset_price:{asset}", U128(100_000_000))

        self.env.emit_event("asset_supported", {
            "asset": asset,
            "is_collateral": is_collateral,
            "ltv": ltv_bps,
        })

    @external
    def set_asset_price(self, caller: Address, asset: Address, price_usd: U128):
        """
        Updates oracle price of asset in USD (8 decimals). Admin-only.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        self.storage.set(f"asset_price:{asset}", price_usd)
        self.env.emit_event("price_updated", {"asset": asset, "price": price_usd})

    # ── Delegator Collateral Management ──────────────────────────────────────

    @external
    def deposit_collateral(self, caller: Address, asset: Address, amount: U128):
        """
        Delegates deposit collateral to back their own or delegatees' credit lines.
        """
        caller.require_auth()
        self._require_initialized()

        config = self._get_asset_config(asset)
        if not config["is_collateral"]:
            raise ContractError.ASSET_NOT_SUPPORTED

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        self.env.transfer(caller, self.env.current_contract(), asset, amount)

        # Convert to WAD
        decimals = config["decimals"]
        amount_wad = amount * (10 ** (18 - decimals))

        user_collateral = self.storage.get(f"collateral:{caller}:{asset}", U128(0))
        self.storage.set(f"collateral:{caller}:{asset}", user_collateral + amount_wad)

        self.env.emit_event("collateral_deposited", {
            "delegator": caller,
            "asset": asset,
            "amount": amount,
        })

    @external
    def withdraw_collateral(self, caller: Address, asset: Address, amount: U128):
        """
        Withdraw collateral if health factor remains above 1.0.
        """
        caller.require_auth()
        self._require_initialized()

        config = self._get_asset_config(asset)
        user_collateral = self.storage.get(f"collateral:{caller}:{asset}", U128(0))

        decimals = config["decimals"]
        amount_wad = amount * (10 ** (18 - decimals))

        if amount_wad > user_collateral:
            raise ContractError.INSUFFICIENT_COLLATERAL

        # Temporary deduct to check health
        self.storage.set(f"collateral:{caller}:{asset}", user_collateral - amount_wad)

        # Evaluate health factor post withdraw
        health = self._get_health_factor(caller)
        if health < WAD:
            self.storage.set(f"collateral:{caller}:{asset}", user_collateral)
            raise ContractError.HEALTH_FACTOR_TOO_LOW

        # Transfer tokens back
        self.env.transfer(self.env.current_contract(), caller, asset, amount)

        self.env.emit_event("collateral_withdrawn", {
            "delegator": caller,
            "asset": asset,
            "amount": amount,
        })

    # ── Credit Line Management ───────────────────────────────────────────────

    @external
    def approve_credit_line(
        self,
        caller: Address,
        delegatee: Address,
        asset: Address,
        limit: U128,
        interest_premium_bps: U128,
        grace_period_seconds: U64,
    ):
        """
        Approves or updates a borrowing limit (credit line) for a delegatee.
        """
        caller.require_auth()
        self._require_initialized()

        self._get_asset_config(asset)  # Ensure supported

        key = f"credit_line:{caller}:{delegatee}:{asset}"
        line = self.storage.get(key, None)

        if line is None:
            line = {
                "limit": limit,
                "borrowed_shares": U128(0),
                "interest_index": WAD,
                "last_update_time": self.env.ledger().timestamp(),
                "premium": interest_premium_bps,
                "grace_period": grace_period_seconds,
                "last_payment_time": self.env.ledger().timestamp(),
                "suspended": False,
            }
            # Register delegatee to delegator's list
            delegatees = self.storage.get(f"delegatees:{caller}", Vec())
            has_delegatee = False
            for d in range(len(delegatees)):
                if delegatees[d] == delegatee:
                    has_delegatee = True
                    break
            if not has_delegatee:
                delegatees.append(delegatee)
                self.storage.set(f"delegatees:{caller}", delegatees)
        else:
            self._accrue_interest(caller, delegatee, asset)
            line = self.storage.get(key)
            line["limit"] = limit
            line["premium"] = interest_premium_bps
            line["grace_period"] = grace_period_seconds

        self.storage.set(key, line)

        self.env.emit_event("credit_line_approved", {
            "delegator": caller,
            "delegatee": delegatee,
            "asset": asset,
            "limit": limit,
        })

    # ── Borrow & Repay ───────────────────────────────────────────────────────

    @external
    def borrow_against_line(self, caller: Address, delegator: Address, asset: Address, amount: U128):
        """
        Delegatee borrows against the delegator's collateral up to their credit line limit.
        """
        caller.require_auth()
        self._require_initialized()

        key = f"credit_line:{delegator}:{caller}:{asset}"
        line = self.storage.get(key, None)
        if line is None:
            raise ContractError.CREDIT_LINE_NOT_FOUND
        if line["suspended"]:
            raise ContractError.CREDIT_LINE_SUSPENDED

        self._accrue_interest(delegator, caller, asset)
        line = self.storage.get(key)

        config = self._get_asset_config(asset)
        decimals = config["decimals"]
        amount_wad = amount * (10 ** (18 - decimals))

        # Check credit line limit
        current_borrow = (line["borrowed_shares"] * line["interest_index"]) // WAD
        if current_borrow + amount_wad > line["limit"]:
            raise ContractError.LIMIT_EXCEEDED

        # Update borrow shares
        shares = (amount_wad * WAD) // line["interest_index"]
        line["borrowed_shares"] += shares
        line["last_payment_time"] = self.env.ledger().timestamp()
        self.storage.set(key, line)

        # Check delegator's health factor post borrow
        health = self._get_health_factor(delegator)
        if health < WAD:
            # Revert borrow shares
            line["borrowed_shares"] -= shares
            self.storage.set(key, line)
            raise ContractError.HEALTH_FACTOR_TOO_LOW

        # Transfer borrow asset from pool (this contract) to delegatee
        self.env.transfer(self.env.current_contract(), caller, asset, amount)

        self.env.emit_event("borrowed_delegated", {
            "delegator": delegator,
            "delegatee": caller,
            "asset": asset,
            "amount": amount,
        })

    @external
    def repay_delegated_debt(self, caller: Address, delegator: Address, asset: Address, amount: U128):
        """
        Repays delegated debt. Can be performed by delegatee or delegator.
        """
        caller.require_auth()
        self._require_initialized()

        key = f"credit_line:{delegator}:{caller}:{asset}"
        line = self.storage.get(key, None)
        if line is None:
            raise ContractError.CREDIT_LINE_NOT_FOUND

        self._accrue_interest(delegator, caller, asset)
        line = self.storage.get(key)

        config = self._get_asset_config(asset)
        decimals = config["decimals"]
        amount_wad = amount * (10 ** (18 - decimals))

        current_borrow = (line["borrowed_shares"] * line["interest_index"]) // WAD
        if current_borrow == U128(0):
            raise ContractError.NO_DEBT

        repay_wad = amount_wad
        actual_repay = amount
        if repay_wad > current_borrow:
            repay_wad = current_borrow
            actual_repay = current_borrow // (10 ** (18 - decimals))

        shares_to_burn = (repay_wad * WAD) // line["interest_index"]
        line["borrowed_shares"] -= shares_to_burn
        line["last_payment_time"] = self.env.ledger().timestamp()

        self.storage.set(key, line)

        # Transfer tokens to contract
        self.env.transfer(caller, self.env.current_contract(), asset, actual_repay)

        self.env.emit_event("repaid_delegated", {
            "delegator": delegator,
            "delegatee": caller,
            "asset": asset,
            "amount": actual_repay,
        })

    # ── Default Cascade ──────────────────────────────────────────────────────

    @external
    def trigger_default_cascade(self, caller: Address, delegatee: Address, asset: Address):
        """
        Delegator triggers a default cascade on a delegatee who missed a repayment
        deadline past the grace period. Suspends credit line and defaults delegatee.
        """
        caller.require_auth()
        self._require_initialized()

        key = f"credit_line:{caller}:{delegatee}:{asset}"
        line = self.storage.get(key, None)
        if line is None:
            raise ContractError.CREDIT_LINE_NOT_FOUND

        self._accrue_interest(caller, delegatee, asset)
        line = self.storage.get(key)

        now = self.env.ledger().timestamp()
        due_limit = line["last_payment_time"] + line["grace_period"]

        if now <= due_limit:
            raise ContractError.GRACE_PERIOD_ACTIVE

        # Cascade Action: Suspend credit line permanently and log default status
        line["suspended"] = True
        self.storage.set(key, line)

        self.env.emit_event("delegatee_defaulted", {
            "delegator": caller,
            "delegatee": delegatee,
            "asset": asset,
            "outstanding_debt": (line["borrowed_shares"] * line["interest_index"]) // WAD,
        })

    # ── View Functions ───────────────────────────────────────────────────────

    @view
    def get_credit_line(self, delegator: Address, delegatee: Address, asset: Address) -> Map:
        """Returns details of a credit line."""
        key = f"credit_line:{delegator}:{delegatee}:{asset}"
        line = self.storage.get(key, None)
        if line is None:
            raise ContractError.CREDIT_LINE_NOT_FOUND
        
        # Calculate updated debt simulation
        now = self.env.ledger().timestamp()
        time_elapsed = U128(now - line["last_update_time"])
        base_rate = self.storage.get("base_borrow_rate", U128(0))
        rate = base_rate + line["premium"]
        interest_factor = (rate * time_elapsed * WAD) // (SECONDS_PER_YEAR * BPS_DENOMINATOR)
        sim_index = (line["interest_index"] * (WAD + interest_factor)) // WAD
        
        current_borrow = (line["borrowed_shares"] * sim_index) // WAD
        return {
            "limit": line["limit"],
            "borrowed": current_borrow,
            "premium": line["premium"],
            "grace_period": line["grace_period"],
            "suspended": line["suspended"],
        }

    @view
    def get_delegator_health(self, delegator: Address) -> U128:
        """Returns the health factor of a delegator."""
        return self._get_health_factor(delegator)

    # ── Internal Helpers ─────────────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _get_asset_config(self, asset: Address) -> Map:
        config = self.storage.get(f"asset_config:{asset}", None)
        if config is None:
            raise ContractError.ASSET_NOT_SUPPORTED
        return config

    def _accrue_interest(self, delegator: Address, delegatee: Address, asset: Address):
        """Accrues interest on the specific credit line."""
        key = f"credit_line:{delegator}:{delegatee}:{asset}"
        line = self.storage.get(key)
        now = self.env.ledger().timestamp()

        time_elapsed = U128(now - line["last_update_time"])
        if time_elapsed == U128(0):
            return

        base_rate = self.storage.get("base_borrow_rate")
        rate = base_rate + line["premium"]  # total annual rate in bps

        # interest_factor = rate * dt / (seconds_per_year * 10000)
        interest_factor = (rate * time_elapsed * WAD) // (SECONDS_PER_YEAR * BPS_DENOMINATOR)

        line["interest_index"] = (line["interest_index"] * (WAD + interest_factor)) // WAD
        line["last_update_time"] = now
        self.storage.set(key, line)

    def _get_health_factor(self, delegator: Address) -> U128:
        """
        Calculates delegator health: Sum(collateral_value * threshold) / Sum(borrowed_debts)
        """
        col_assets = self.storage.get("collateral_assets", Vec())
        debt_assets = self.storage.get("debt_assets", Vec())

        total_collateral_threshold_usd = U128(0)
        total_debt_usd = U128(0)

        # 1. Sum up all collateral
        for i in range(len(col_assets)):
            asset = col_assets[i]
            config = self._get_asset_config(asset)
            price = self.storage.get(f"asset_price:{asset}", U128(0))
            if price == U128(0):
                continue

            user_collateral = self.storage.get(f"collateral:{delegator}:{asset}", U128(0))
            if user_collateral > U128(0):
                collateral_usd = (user_collateral * price) // U128(100_000_000)
                total_collateral_threshold_usd += (collateral_usd * config["liq_threshold"]) // BPS_DENOMINATOR

        # 2. Sum up all delegated borrow debts (for this delegator across all delegatees)
        # In a realistic contract, we map delegatees. For simplicity and gas, we query active lines.
        # Let's check all debt assets approved for active credit lines.
        # We search matching storage keys using custom iteration or simulated registry.
        # To make it self-contained and clean, let's look at all supported debt assets
        # and sum up line records.
        # Since we have dynamic delegatees, we must track delegatees registered to a delegator.
        # Let's store a list of delegatees for the delegator.
        delegatees = self.storage.get(f"delegatees:{delegator}", Vec())
        
        # Let's update approve_credit_line to register delegatees
        # Wait, let's just make sure we registers them:
        # In a view we can dynamically sum them if registered.
        for d in range(len(delegatees)):
            delegatee = delegatees[d]
            for a in range(len(debt_assets)):
                asset = debt_assets[a]
                key = f"credit_line:{delegator}:{delegatee}:{asset}"
                line = self.storage.get(key, None)
                if line is not None and line["borrowed_shares"] > U128(0):
                    # Accrued price
                    price = self.storage.get(f"asset_price:{asset}", U128(0))
                    if price > U128(0):
                        current_borrow = (line["borrowed_shares"] * line["interest_index"]) // WAD
                        borrow_usd = (current_borrow * price) // U128(100_000_000)
                        total_debt_usd += borrow_usd

        if total_debt_usd == U128(0):
            return WAD * U128(100)  # Infinite health

        return (total_collateral_threshold_usd * WAD) // total_debt_usd
