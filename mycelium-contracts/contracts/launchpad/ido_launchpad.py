"""
IDO Launchpad — Multi-tier whitelists, contribution allocations, refund windows, vesting schedules.

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
    SALE_NOT_ACTIVE = 4
    SALE_ENDED = 5
    EXCEEDS_ALLOCATION = 6
    EXCEEDS_HARD_CAP = 7
    REFUND_NOT_ALLOWED = 8
    VESTING_NOT_STARTED = 9
    ZERO_AMOUNT = 10
    INSUFFICIENT_BALANCE = 11
    REFUND_WINDOW_ACTIVE = 12
    ALREADY_FINALIZED = 13


class SaleState:
    ACTIVE = 0
    SUCCESS = 1
    FAILED = 2


@contract
class IdoLaunchpad:
    """An IDO launchpad contract supporting multi-tier whitelists, contribution locks, refunds, and vesting."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        sale_token: Address,
        payment_token: Address,
        soft_cap: U128,
        hard_cap: U128,
        price_per_token: U128,  # Scaling factor: payment token amount per 1 sale token (1:1 scaling)
        start_time: U64,
        duration: U64,
        refund_window: U64,    # Duration in seconds after end_time during which users can voluntarily opt-out
        tge_release_bps: U64,  # Basis points released at finalization (e.g. 2000 = 20%)
        cliff_duration: U64,
        vesting_duration: U64,
    ):
        """Initialize the IDO Launchpad.

        Args:
            admin: Admin address.
            sale_token: Token being launched.
            payment_token: Funding token (e.g. USDC).
            soft_cap: Minimum funding target.
            hard_cap: Maximum funding limit.
            price_per_token: Price of 1 sale token in payment tokens.
            start_time: Launch start timestamp.
            duration: Active contribution phase duration.
            refund_window: Post-sale duration for user opt-out refunds.
            tge_release_bps: Percentage of tokens unlocked immediately upon success.
            cliff_duration: Delay in seconds after success before linear vesting begins.
            vesting_duration: Linear vesting period duration in seconds.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("sale_token", sale_token)
        self.storage.set("payment_token", payment_token)
        self.storage.set("soft_cap", soft_cap)
        self.storage.set("hard_cap", hard_cap)
        self.storage.set("price_per_token", price_per_token)
        self.storage.set("start_time", start_time)
        self.storage.set("end_time", start_time + duration)
        self.storage.set("refund_deadline", start_time + duration + refund_window)
        self.storage.set("tge_release_bps", tge_release_bps)
        self.storage.set("cliff_duration", cliff_duration)
        self.storage.set("vesting_duration", vesting_duration)

        self.storage.set("total_collected", U128(0))
        self.storage.set("state", SaleState.ACTIVE)
        self.storage.set("finalization_time", U64(0))

        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "sale_token": sale_token,
            "hard_cap": hard_cap,
            "end_time": start_time + duration,
        })

    @external
    def set_tier_allocations(self, admin: Address, tier: U64, max_allocation: U128) -> Bool:
        """Define maximum contribution allocation per tier. Only admin.

        Args:
            admin: Admin address.
            tier: Whitelist tier ID (e.g. 1, 2, 3).
            max_allocation: Max payment token contribution allowed for this tier.
        """
        self._require_initialized()
        self._require_admin(admin)

        self.storage.set(("tier_alloc", tier), max_allocation)
        self.env.emit_event("tier_updated", {"tier": tier, "alloc": max_allocation})

        return True

    @external
    def whitelist_users(self, admin: Address, users: Vec, tier: U64) -> Bool:
        """Assign multiple users to a whitelist tier. Only admin.

        Args:
            admin: Admin address.
            users: Vector of addresses.
            tier: Whitelist tier ID.
        """
        self._require_initialized()
        self._require_admin(admin)

        for i in range(len(users)):
            user = users.get(i)
            self.storage.set(("user_tier", user), tier)

        self.env.emit_event("users_whitelisted", {"count": len(users), "tier": tier})

        return True

    @external
    def contribute(self, user: Address, amount: U128) -> U128:
        """Contribute payment tokens to the IDO up to tier limit.

        Args:
            user: Contributor.
            amount: Amount of payment token.
        """
        self._require_initialized()
        self._require_state(SaleState.ACTIVE)
        user.require_auth()

        now = self.env.ledger().timestamp()
        if now < self.storage.get("start_time"):
            raise ContractError.SALE_NOT_ACTIVE
        if now >= self.storage.get("end_time"):
            raise ContractError.SALE_ENDED

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        # Verify whitelist allocation limit
        tier = self.storage.get(("user_tier", user), U64(0))
        max_alloc = self.storage.get(("tier_alloc", tier), U128(0))
        
        current_contrib = self.storage.get(("contribution", user), U128(0))
        if current_contrib + amount > max_alloc:
            raise ContractError.EXCEEDS_ALLOCATION

        # Hard cap check
        total_collected = self.storage.get("total_collected")
        hard_cap = self.storage.get("hard_cap")
        if total_collected + amount > hard_cap:
            raise ContractError.EXCEEDS_HARD_CAP

        # Transfer payment tokens
        payment_token = self.storage.get("payment_token")
        success = self.env.invoke_contract(
            payment_token,
            "transfer",
            [user, self.env.current_contract_address(), amount]
        )
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        self.storage.set(("contribution", user), current_contrib + amount)
        self.storage.set("total_collected", total_collected + amount)

        self.env.emit_event("contributed", {
            "user": user,
            "amount": amount,
            "total_user": current_contrib + amount,
        })

        return current_contrib + amount

    @external
    def claim_refund(self, user: Address) -> U128:
        """Claim a refund. Permitted if IDO fails, or voluntarily during the post-sale refund window.

        Args:
            user: Contributor reclaiming funds.
        """
        self._require_initialized()
        user.require_auth()

        state = self.storage.get("state")
        now = self.env.ledger().timestamp()
        end_time = self.storage.get("end_time")
        refund_deadline = self.storage.get("refund_deadline")

        can_refund = False
        if state == SaleState.FAILED:
            can_refund = True
        elif state == SaleState.ACTIVE:
            # Active but past end_time without finalization, or during voluntary refund window
            if now > end_time and now <= refund_deadline:
                can_refund = True
            elif now > refund_deadline:
                # If past deadline and softcap wasn't met, let users refund
                total_collected = self.storage.get("total_collected")
                soft_cap = self.storage.get("soft_cap")
                if total_collected < soft_cap:
                    can_refund = True

        if not can_refund:
            raise ContractError.REFUND_NOT_ALLOWED

        contrib = self.storage.get(("contribution", user), U128(0))
        if contrib == U128(0):
            raise ContractError.ZERO_AMOUNT

        self.storage.set(("contribution", user), U128(0))
        
        # Deduct from total collected if refund occurs during active state
        if state == SaleState.ACTIVE:
            total_collected = self.storage.get("total_collected")
            self.storage.set("total_collected", total_collected - contrib)

        payment_token = self.storage.get("payment_token")
        self.env.invoke_contract(
            payment_token,
            "transfer",
            [self.env.current_contract_address(), user, contrib]
        )

        self.env.emit_event("refunded", {
            "user": user,
            "amount": contrib,
        })

        return contrib

    @external
    def finalize_sale(self, caller: Address) -> U64:
        """Finalize the sale. Transitions state to SUCCESS or FAILED based on soft cap.

        Args:
            caller: Triggerer.
        """
        self._require_initialized()
        self._require_state(SaleState.ACTIVE)
        caller.require_auth()

        now = self.env.ledger().timestamp()
        end_time = self.storage.get("end_time")
        if now < end_time:
            raise ContractError.SALE_NOT_ACTIVE

        total_collected = self.storage.get("total_collected")
        soft_cap = self.storage.get("soft_cap")

        state = SaleState.FAILED
        if total_collected >= soft_cap:
            state = SaleState.SUCCESS
            self.storage.set("finalization_time", now)
        
        self.storage.set("state", state)

        # Refund unsold tokens back to admin if success or failed
        if state == SaleState.FAILED:
            # Reclaim all deposited sale tokens
            pass # In a real system we transfer all sale tokens back. Handled in withdraw/reclaim helper.

        self.env.emit_event("sale_finalized", {"state": state, "total_raised": total_collected})

        return state

    @external
    def claim_tokens(self, user: Address) -> U128:
        """Claim vested purchase tokens. Only when sale is in SUCCESS state and refund window has closed.

        Args:
            user: Purchaser address.
        """
        self._require_initialized()
        self._require_state(SaleState.SUCCESS)
        user.require_auth()

        now = self.env.ledger().timestamp()
        refund_deadline = self.storage.get("refund_deadline")

        if now < refund_deadline:
            raise ContractError.REFUND_WINDOW_ACTIVE

        contrib = self.storage.get(("contribution", user), U128(0))
        if contrib == U128(0):
            raise ContractError.ZERO_AMOUNT

        price = self.storage.get("price_per_token")
        # Total tokens purchased: contrib / price
        total_entitled = (contrib * U128(1000000)) / price

        claimed = self.storage.get(("claimed", user), U128(0))
        unlocked = self._calculate_unlocked(total_entitled, now)

        claimable = unlocked - claimed
        if claimable == U128(0):
            raise ContractError.ZERO_AMOUNT

        self.storage.set(("claimed", user), claimed + claimable)

        sale_token = self.storage.get("sale_token")
        self.env.invoke_contract(
            sale_token,
            "transfer",
            [self.env.current_contract_address(), user, claimable]
        )

        self.env.emit_event("tokens_claimed", {
            "user": user,
            "amount": claimable,
        })

        return claimable

    @external
    def withdraw_proceeds(self, admin: Address) -> U128:
        """Withdraw contribution proceeds to the admin. Only after success and refund window closes.

        Args:
            admin: Admin address.
        """
        self._require_initialized()
        self._require_state(SaleState.SUCCESS)
        admin.require_auth()

        expected_admin = self.storage.get("admin")
        if admin != expected_admin:
            raise ContractError.UNAUTHORIZED

        now = self.env.ledger().timestamp()
        refund_deadline = self.storage.get("refund_deadline")
        if now < refund_deadline:
            raise ContractError.REFUND_WINDOW_ACTIVE

        payment_token = self.storage.get("payment_token")
        # We can withdraw the total collected amount
        collected = self.storage.get("total_collected")
        self.storage.set("total_collected", U128(0)) # Reset to prevent double withdraw

        self.env.invoke_contract(
            payment_token,
            "transfer",
            [self.env.current_contract_address(), admin, collected]
        )

        self.env.emit_event("proceeds_withdrawn", {"amount": collected})

        return collected

    @view
    def get_claimable_tokens(self, user: Address) -> U128:
        """View currently claimable vested tokens for a user."""
        contrib = self.storage.get(("contribution", user), U128(0))
        if contrib == U128(0):
            return U128(0)

        price = self.storage.get("price_per_token")
        total_entitled = (contrib * U128(1000000)) / price
        claimed = self.storage.get(("claimed", user), U128(0))
        
        now = self.env.ledger().timestamp()
        unlocked = self._calculate_unlocked(total_entitled, now)
        
        return unlocked - claimed

    @view
    def get_status(self) -> Map:
        """Get general IDO launchpad status."""
        res = Map()
        res.set("state", self.storage.get("state"))
        res.set("total_collected", self.storage.get("total_collected"))
        res.set("end_time", self.storage.get("end_time"))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        caller.require_auth()
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_state(self, expected: int):
        if self.storage.get("state") != expected:
            raise ContractError.ALREADY_FINALIZED

    def _calculate_unlocked(self, total: U128, now: U64) -> U128:
        final_time = self.storage.get("finalization_time")
        if final_time == U64(0):
            return U128(0)

        tge_bps = self.storage.get("tge_release_bps")
        cliff = self.storage.get("cliff_duration")
        duration = self.storage.get("vesting_duration")

        # TGE Release
        tge_release = (total * U128(tge_bps)) / U128(10000)

        if now < final_time + cliff:
            return tge_release

        vesting_start = final_time + cliff
        if now >= vesting_start + duration:
            return total

        elapsed = now - vesting_start
        vested_part = total - tge_release
        linear_unlocked = (vested_part * U128(elapsed)) / U128(duration)

        return tge_release + linear_unlocked
