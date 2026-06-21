"""
LiquidStaking — Liquid staking derivative with exchange-rate-based stToken minting.

Mycelium Smart Contract for Stellar

Features:
- Stake native tokens and receive stToken at a dynamic exchange rate
- Exchange rate increases over time from protocol rewards
- Unbonding queue with configurable delay
- Instant unstake via liquidity pool at a discount
- Validator set management (add/remove/rebalance)
- Slashing event handling with exchange rate adjustment
- Protocol fee collection on rewards
- First-depositor attack mitigation via dead shares
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
    ZERO_AMOUNT = 4
    INSUFFICIENT_BALANCE = 5
    VALIDATOR_ALREADY_REGISTERED = 6
    VALIDATOR_NOT_FOUND = 7
    VALIDATOR_NOT_ACTIVE = 8
    UNBONDING_NOT_READY = 9
    UNBONDING_NOT_FOUND = 10
    LIQUIDITY_POOL_EMPTY = 11
    EXCHANGE_RATE_ZERO = 12
    MAX_VALIDATORS_REACHED = 13
    SLASH_TOO_LARGE = 14
    UNBONDING_QUEUE_OVERFLOW = 15
    INSTANT_UNSTAKE_DISABLED = 16
    INSUFFICIENT_POOL_LIQUIDITY = 17
    ZERO_SHARES_MINTED = 18
    VALIDATOR_HAS_DELEGATIONS = 19
    FEE_TOO_HIGH = 20


# ── Constants ────────────────────────────────────────────────────────────────

PRECISION = U128(1_000_000_000_000_000_000)  # 1e18
DEAD_SHARES = U128(1_000)  # lock on first deposit to prevent donation attack
MAX_VALIDATORS = U64(50)
MAX_PENDING_UNBONDS = U64(500)
UNBONDING_PERIOD = U64(1_209_600)  # 14 days in seconds
INSTANT_UNSTAKE_DISCOUNT_BPS = U64(200)  # 2 %
MAX_FEE_BPS = U64(2000)  # 20 %


@contract
class LiquidStaking:
    """
    Liquid staking protocol.  Users deposit native tokens, receive stTokens
    at the current exchange rate, and can later redeem them via the unbonding
    queue or instantly through a liquidity pool at a small discount.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ── Initialisation ───────────────────────────────────────────────────

    @external
    def initialize(
        self,
        admin: Address,
        native_token: Address,
        st_token: Address,
        fee_recipient: Address,
        protocol_fee_bps: U64,
    ):
        """Bootstrap the liquid-staking protocol."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED
        admin.require_auth()

        if protocol_fee_bps > MAX_FEE_BPS:
            raise ContractError.FEE_TOO_HIGH

        self.storage.set("admin", admin)
        self.storage.set("native_token", native_token)
        self.storage.set("st_token", st_token)
        self.storage.set("fee_recipient", fee_recipient)
        self.storage.set("protocol_fee_bps", protocol_fee_bps)
        self.storage.set("total_pooled", U128(0))
        self.storage.set("total_shares", U128(0))
        self.storage.set("validator_count", U64(0))
        self.storage.set("unbond_nonce", U64(0))
        self.storage.set("pending_unbond_count", U64(0))
        self.storage.set("instant_unstake_enabled", True)
        self.storage.set("pool_liquidity", U128(0))
        self.storage.set("total_fees_collected", U128(0))
        self.storage.set("first_deposit_done", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "native_token": native_token,
            "st_token": st_token,
            "protocol_fee_bps": protocol_fee_bps,
        })

    # ── Staking ──────────────────────────────────────────────────────────

    @external
    def stake(self, user: Address, amount: U128):
        """
        Deposit native tokens and mint stTokens at the current exchange rate.
        On first deposit, DEAD_SHARES are permanently locked to prevent the
        first-depositor donation attack.
        """
        user.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        native_token = self.storage.get("native_token")
        self.env.transfer(user, self.env.current_contract(), native_token, amount)

        total_pooled = self.storage.get("total_pooled")
        total_shares = self.storage.get("total_shares")

        # First-deposit dead-share mitigation
        first_deposit_done = self.storage.get("first_deposit_done")
        if not first_deposit_done:
            shares_minted = amount  # 1:1 on first deposit
            if shares_minted <= DEAD_SHARES:
                raise ContractError.ZERO_SHARES_MINTED
            # Lock dead shares by not assigning them to anyone
            shares_minted -= DEAD_SHARES
            total_shares += DEAD_SHARES
            total_pooled += amount
            self.storage.set("first_deposit_done", True)
        else:
            # shares = amount * totalShares / totalPooled
            shares_minted = (amount * total_shares) / total_pooled
            if shares_minted == U128(0):
                raise ContractError.ZERO_SHARES_MINTED
            total_pooled += amount

        total_shares += shares_minted
        self.storage.set("total_pooled", total_pooled)
        self.storage.set("total_shares", total_shares)

        # Mint stTokens to user
        st_token = self.storage.get("st_token")
        self.env.mint(st_token, user, shares_minted)

        self.env.emit_event("staked", {
            "user": user,
            "amount": amount,
            "shares_minted": shares_minted,
            "exchange_rate": self._exchange_rate(),
        })

    # ── Unbonding Queue ──────────────────────────────────────────────────

    @external
    def request_unbond(self, user: Address, shares: U128):
        """
        Burn stTokens and queue an unbonding request that matures after
        the unbonding period.
        """
        user.require_auth()
        self._require_initialized()

        if shares == U128(0):
            raise ContractError.ZERO_AMOUNT

        pending = self.storage.get("pending_unbond_count")
        if pending >= MAX_PENDING_UNBONDS:
            raise ContractError.UNBONDING_QUEUE_OVERFLOW

        total_pooled = self.storage.get("total_pooled")
        total_shares = self.storage.get("total_shares")
        token_amount = (shares * total_pooled) / total_shares

        # Burn shares
        st_token = self.storage.get("st_token")
        self.env.burn(st_token, user, shares)

        total_shares -= shares
        total_pooled -= token_amount
        self.storage.set("total_shares", total_shares)
        self.storage.set("total_pooled", total_pooled)

        nonce = self.storage.get("unbond_nonce")
        now = self.env.ledger().timestamp()
        maturity = now + UNBONDING_PERIOD

        unbond = {
            "user": user,
            "token_amount": token_amount,
            "shares_burned": shares,
            "request_time": now,
            "maturity": maturity,
            "claimed": False,
            "slashed_amount": U128(0),
        }
        self.storage.set(f"unbond:{nonce}", unbond)
        self.storage.set("unbond_nonce", nonce + U64(1))
        self.storage.set("pending_unbond_count", pending + U64(1))

        # Track user unbonds list
        user_unbonds = self.storage.get(f"user_unbonds:{user}", [])
        user_unbonds.append(nonce)
        self.storage.set(f"user_unbonds:{user}", user_unbonds)

        self.env.emit_event("unbond_requested", {
            "user": user,
            "nonce": nonce,
            "shares_burned": shares,
            "token_amount": token_amount,
            "maturity": maturity,
        })

    @external
    def claim_unbond(self, user: Address, nonce: U64):
        """Claim a matured unbonding request."""
        user.require_auth()
        self._require_initialized()

        unbond = self.storage.get(f"unbond:{nonce}", None)
        if unbond is None:
            raise ContractError.UNBONDING_NOT_FOUND
        if unbond["user"] != user:
            raise ContractError.UNAUTHORIZED
        if unbond["claimed"]:
            raise ContractError.UNBONDING_NOT_FOUND

        now = self.env.ledger().timestamp()
        if now < unbond["maturity"]:
            raise ContractError.UNBONDING_NOT_READY

        payout = unbond["token_amount"] - unbond["slashed_amount"]
        unbond["claimed"] = True
        self.storage.set(f"unbond:{nonce}", unbond)

        pending = self.storage.get("pending_unbond_count")
        self.storage.set("pending_unbond_count", pending - U64(1))

        native_token = self.storage.get("native_token")
        self.env.transfer(self.env.current_contract(), user, native_token, payout)

        self.env.emit_event("unbond_claimed", {
            "user": user,
            "nonce": nonce,
            "payout": payout,
        })

    # ── Instant Unstake ──────────────────────────────────────────────────

    @external
    def instant_unstake(self, user: Address, shares: U128):
        """
        Instantly redeem stTokens through the liquidity pool at a discount.
        """
        user.require_auth()
        self._require_initialized()

        if not self.storage.get("instant_unstake_enabled", False):
            raise ContractError.INSTANT_UNSTAKE_DISABLED
        if shares == U128(0):
            raise ContractError.ZERO_AMOUNT

        total_pooled = self.storage.get("total_pooled")
        total_shares = self.storage.get("total_shares")
        gross_value = (shares * total_pooled) / total_shares
        discount = (gross_value * U128(INSTANT_UNSTAKE_DISCOUNT_BPS)) / U128(10000)
        net_payout = gross_value - discount

        pool_liq = self.storage.get("pool_liquidity")
        if net_payout > pool_liq:
            raise ContractError.INSUFFICIENT_POOL_LIQUIDITY

        st_token = self.storage.get("st_token")
        self.env.burn(st_token, user, shares)

        total_shares -= shares
        total_pooled -= gross_value
        self.storage.set("total_shares", total_shares)
        self.storage.set("total_pooled", total_pooled)
        self.storage.set("pool_liquidity", pool_liq - net_payout)

        native_token = self.storage.get("native_token")
        self.env.transfer(self.env.current_contract(), user, native_token, net_payout)

        self.env.emit_event("instant_unstake", {
            "user": user,
            "shares_burned": shares,
            "gross_value": gross_value,
            "discount": discount,
            "net_payout": net_payout,
        })

    @external
    def add_pool_liquidity(self, provider: Address, amount: U128):
        """Provide liquidity for instant-unstake pool."""
        provider.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        native_token = self.storage.get("native_token")
        self.env.transfer(provider, self.env.current_contract(), native_token, amount)

        pool_liq = self.storage.get("pool_liquidity")
        self.storage.set("pool_liquidity", pool_liq + amount)

        self.env.emit_event("pool_liquidity_added", {
            "provider": provider,
            "amount": amount,
        })

    # ── Validator Management ─────────────────────────────────────────────

    @external
    def add_validator(self, caller: Address, validator: Address, weight: U64):
        """Register a new validator with an allocation weight."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        count = self.storage.get("validator_count")
        if count >= MAX_VALIDATORS:
            raise ContractError.MAX_VALIDATORS_REACHED

        existing = self.storage.get(f"validator:{validator}", None)
        if existing is not None:
            raise ContractError.VALIDATOR_ALREADY_REGISTERED

        validator_data = {
            "address": validator,
            "weight": weight,
            "delegated": U128(0),
            "active": True,
        }
        self.storage.set(f"validator:{validator}", validator_data)
        validators = self.storage.get("validator_list", [])
        validators.append(validator)
        self.storage.set("validator_list", validators)
        self.storage.set("validator_count", count + U64(1))

        self.env.emit_event("validator_added", {
            "validator": validator,
            "weight": weight,
        })

    @external
    def remove_validator(self, caller: Address, validator: Address):
        """Deactivate a validator. Funds must be re-delegated first."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        data = self.storage.get(f"validator:{validator}", None)
        if data is None:
            raise ContractError.VALIDATOR_NOT_FOUND
        if data["delegated"] > U128(0):
            raise ContractError.VALIDATOR_HAS_DELEGATIONS

        data["active"] = False
        self.storage.set(f"validator:{validator}", data)

        validators = self.storage.get("validator_list", [])
        validators = [v for v in validators if v != validator]
        self.storage.set("validator_list", validators)
        count = self.storage.get("validator_count")
        self.storage.set("validator_count", count - U64(1))

        self.env.emit_event("validator_removed", {"validator": validator})

    # ── Reward / Slashing ────────────────────────────────────────────────

    @external
    def report_rewards(self, caller: Address, reward_amount: U128):
        """
        Called by the oracle/admin to report staking rewards earned.
        Increases total_pooled (thus the exchange rate) after taking fees.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        if reward_amount == U128(0):
            return

        fee_bps = self.storage.get("protocol_fee_bps")
        fee = (reward_amount * U128(fee_bps)) / U128(10000)
        net_reward = reward_amount - fee

        total_pooled = self.storage.get("total_pooled")
        self.storage.set("total_pooled", total_pooled + net_reward)

        total_fees = self.storage.get("total_fees_collected")
        self.storage.set("total_fees_collected", total_fees + fee)

        self.env.emit_event("rewards_reported", {
            "gross_reward": reward_amount,
            "fee": fee,
            "net_reward": net_reward,
            "new_exchange_rate": self._exchange_rate(),
        })

    @external
    def report_slash(self, caller: Address, validator: Address, slash_amount: U128):
        """
        Handle a slashing event.  Reduces total_pooled and adjusts pending
        unbonds proportionally.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        data = self.storage.get(f"validator:{validator}", None)
        if data is None:
            raise ContractError.VALIDATOR_NOT_FOUND

        total_pooled = self.storage.get("total_pooled")
        if slash_amount > total_pooled:
            raise ContractError.SLASH_TOO_LARGE

        # Reduce validator delegation record
        slashed_from_val = slash_amount if slash_amount <= data["delegated"] else data["delegated"]
        data["delegated"] -= slashed_from_val
        self.storage.set(f"validator:{validator}", data)

        self.storage.set("total_pooled", total_pooled - slash_amount)

        # Apply proportional slash to pending unbonds
        nonce = self.storage.get("unbond_nonce")
        for i in range(nonce):
            unbond = self.storage.get(f"unbond:{i}", None)
            if unbond is not None and not unbond["claimed"]:
                proportional = (unbond["token_amount"] * slash_amount) / total_pooled
                unbond["slashed_amount"] += proportional
                self.storage.set(f"unbond:{i}", unbond)

        self.env.emit_event("slash_reported", {
            "validator": validator,
            "slash_amount": slash_amount,
            "new_exchange_rate": self._exchange_rate(),
        })

    # ── Admin ────────────────────────────────────────────────────────────

    @external
    def set_fee(self, caller: Address, new_fee_bps: U64):
        """Update the protocol fee percentage."""
        caller.require_auth()
        self._require_admin(caller)

        if new_fee_bps > MAX_FEE_BPS:
            raise ContractError.FEE_TOO_HIGH

        self.storage.set("protocol_fee_bps", new_fee_bps)
        self.env.emit_event("fee_updated", {"new_fee_bps": new_fee_bps})

    @external
    def toggle_instant_unstake(self, caller: Address, enabled: Bool):
        """Enable or disable the instant-unstake facility."""
        caller.require_auth()
        self._require_admin(caller)
        self.storage.set("instant_unstake_enabled", enabled)
        self.env.emit_event("instant_unstake_toggled", {"enabled": enabled})

    # ── Views ────────────────────────────────────────────────────────────

    @view
    def get_exchange_rate(self) -> U128:
        """Current stToken → native exchange rate (scaled by PRECISION)."""
        return self._exchange_rate()

    @view
    def get_total_pooled(self) -> U128:
        return self.storage.get("total_pooled", U128(0))

    @view
    def get_total_shares(self) -> U128:
        return self.storage.get("total_shares", U128(0))

    @view
    def get_user_unbonds(self, user: Address) -> Vec:
        return self.storage.get(f"user_unbonds:{user}", [])

    @view
    def get_unbond(self, nonce: U64) -> Map:
        unbond = self.storage.get(f"unbond:{nonce}", None)
        if unbond is None:
            raise ContractError.UNBONDING_NOT_FOUND
        return unbond

    @view
    def get_validator(self, validator: Address) -> Map:
        data = self.storage.get(f"validator:{validator}", None)
        if data is None:
            raise ContractError.VALIDATOR_NOT_FOUND
        return data

    @view
    def get_pool_liquidity(self) -> U128:
        return self.storage.get("pool_liquidity", U128(0))

    @view
    def get_protocol_info(self) -> Map:
        return {
            "total_pooled": self.storage.get("total_pooled"),
            "total_shares": self.storage.get("total_shares"),
            "exchange_rate": self._exchange_rate(),
            "protocol_fee_bps": self.storage.get("protocol_fee_bps"),
            "validator_count": self.storage.get("validator_count"),
            "pending_unbond_count": self.storage.get("pending_unbond_count"),
            "pool_liquidity": self.storage.get("pool_liquidity"),
            "total_fees_collected": self.storage.get("total_fees_collected"),
        }

    # ── Internals ────────────────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        if caller != self.storage.get("admin"):
            raise ContractError.UNAUTHORIZED

    def _exchange_rate(self) -> U128:
        total_shares = self.storage.get("total_shares", U128(0))
        total_pooled = self.storage.get("total_pooled", U128(0))
        if total_shares == U128(0):
            return PRECISION  # 1:1 default
        return (total_pooled * PRECISION) / total_shares
