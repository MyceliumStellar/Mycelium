"""
Liquidity Generation — LGE pool, collect stablecoin contribution, deposit locked LP to DEX, lockup LP token vesting, distributes project tokens.

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
    LGE_NOT_ACTIVE = 4
    LGE_ACTIVE = 5
    ZERO_AMOUNT = 6
    NOT_FUNDED = 7
    ALREADY_EXECUTED = 8
    NO_CONTRIBUTION = 9
    NOT_EXECUTED = 10
    ALREADY_CLAIMED = 11
    NOTHING_TO_VEST = 12
    INVALID_TIME_RANGE = 13
    LGE_FAILED = 14

@contract
class LiquidityGeneration:
    """A Liquidity Generation Event (LGE) contract collecting stablecoins to establish locked LP on a DEX."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        stablecoin: Address,
        project_token: Address,
        dex_amm: Address,
        lp_token: Address,
        treasury: Address,
        contrib_start: U64,
        contrib_end: U64,
        project_tokens_to_distribute: U128,
        project_tokens_for_liquidity: U128,
        lp_vesting_duration: U64,
    ):
        """Initialize the LGE contract.

        Args:
            admin: Admin address.
            stablecoin: Address of the stablecoin used for contribution (e.g. USDC).
            project_token: Address of the project token being launched.
            dex_amm: DEX AMM router/pool address.
            lp_token: LP token address representing the AMM share.
            treasury: Address that receives vested LP tokens (team treasury).
            contrib_start: Contribution phase start timestamp.
            contrib_end: Contribution phase end timestamp.
            project_tokens_to_distribute: Amount of project tokens for participants.
            project_tokens_for_liquidity: Amount of project tokens to match stablecoins in DEX LP.
            lp_vesting_duration: Duration in seconds for LP token vesting.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if contrib_start >= contrib_end:
            raise ContractError.INVALID_TIME_RANGE

        self.storage.set("admin", admin)
        self.storage.set("stablecoin", stablecoin)
        self.storage.set("project_token", project_token)
        self.storage.set("dex_amm", dex_amm)
        self.storage.set("lp_token", lp_token)
        self.storage.set("treasury", treasury)
        self.storage.set("contrib_start", contrib_start)
        self.storage.set("contrib_end", contrib_end)
        
        self.storage.set("tokens_to_distribute", project_tokens_to_distribute)
        self.storage.set("tokens_for_liquidity", project_tokens_for_liquidity)
        self.storage.set("lp_vesting_duration", lp_vesting_duration)

        self.storage.set("total_stablecoin_contributed", U128(0))
        self.storage.set("project_tokens_funded", False)
        self.storage.set("liquidity_generated", False)
        self.storage.set("total_lp_received", U128(0))
        self.storage.set("lp_withdrawn", U128(0))
        self.storage.set("canceled", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "stablecoin": stablecoin,
            "project_token": project_token,
            "contrib_start": contrib_start,
            "contrib_end": contrib_end,
        })

    @external
    def fund_project_tokens(self, admin: Address):
        """Pre-fund the contract with necessary project tokens for distribution and liquidity. (Admin only)

        Args:
            admin: Admin address.
        """
        self._require_initialized()
        admin.require_auth()
        self._require_admin(admin)

        if self.storage.get("project_tokens_funded", False):
            raise ContractError.ALREADY_EXECUTED

        total_needed = self.storage.get("tokens_to_distribute") + self.storage.get("tokens_for_liquidity")
        project_token = self.storage.get("project_token")

        self.env.invoke_contract(
            project_token,
            "transfer",
            [admin, self.env.current_contract_address(), total_needed]
        )

        self.storage.set("project_tokens_funded", True)
        self.env.emit_event("project_tokens_funded", {"amount": total_needed})

    @external
    def contribute(self, caller: Address, amount: U128) -> U128:
        """Contribute stablecoins during the contribution phase.

        Args:
            caller: Contributor address.
            amount: Amount of stablecoins to contribute.
        """
        self._require_initialized()
        caller.require_auth()

        if self.storage.get("canceled", False):
            raise ContractError.LGE_FAILED

        now = self.env.ledger().timestamp()
        start = self.storage.get("contrib_start")
        end = self.storage.get("contrib_end")

        if now < start or now >= end:
            raise ContractError.LGE_NOT_ACTIVE

        if amount == 0:
            raise ContractError.ZERO_AMOUNT

        stablecoin = self.storage.get("stablecoin")
        self.env.invoke_contract(
            stablecoin,
            "transfer",
            [caller, self.env.current_contract_address(), amount]
        )

        user_contrib = self.storage.get(("contrib", caller), U128(0))
        self.storage.set(("contrib", caller), user_contrib + amount)

        total_contrib = self.storage.get("total_stablecoin_contributed")
        self.storage.set("total_stablecoin_contributed", total_contrib + amount)

        self.env.emit_event("contributed", {
            "user": caller,
            "amount": amount,
            "total_user_contrib": user_contrib + amount,
        })

        return amount

    @external
    def generate_liquidity(self) -> U128:
        """Add collected stablecoins and project tokens to DEX pool to generate LP.

        Can only be called after contribution period has ended.
        """
        self._require_initialized()

        if self.storage.get("liquidity_generated", False):
            raise ContractError.ALREADY_EXECUTED

        if self.storage.get("canceled", False):
            raise ContractError.LGE_FAILED

        now = self.env.ledger().timestamp()
        end = self.storage.get("contrib_end")
        if now < end:
            raise ContractError.LGE_ACTIVE

        if not self.storage.get("project_tokens_funded", False):
            raise ContractError.NOT_FUNDED

        total_stablecoins = self.storage.get("total_stablecoin_contributed")
        if total_stablecoins == 0:
            self.storage.set("canceled", True)
            raise ContractError.NO_CONTRIBUTION

        project_tokens_lp = self.storage.get("tokens_for_liquidity")
        stablecoin = self.storage.get("stablecoin")
        project_token = self.storage.get("project_token")
        dex_amm = self.storage.get("dex_amm")

        # Approve DEX to spend stablecoins and project tokens
        self.env.invoke_contract(
            stablecoin,
            "approve",
            [self.env.current_contract_address(), dex_amm, total_stablecoins]
        )
        self.env.invoke_contract(
            project_token,
            "approve",
            [self.env.current_contract_address(), dex_amm, project_tokens_lp]
        )

        # Deposit into AMM
        # We assume interface add_liquidity(token_a, token_b, amount_a, amount_b, recipient)
        # returns LP tokens minted
        lp_tokens_minted = self.env.invoke_contract(
            dex_amm,
            "add_liquidity",
            [
                stablecoin,
                project_token,
                total_stablecoins,
                project_tokens_lp,
                self.env.current_contract_address()
            ]
        )

        self.storage.set("liquidity_generated", True)
        self.storage.set("total_lp_received", lp_tokens_minted)
        self.storage.set("lp_vesting_start", now)

        self.env.emit_event("liquidity_generated", {
            "stablecoins": total_stablecoins,
            "project_tokens": project_tokens_lp,
            "lp_minted": lp_tokens_minted,
        })

        return lp_tokens_minted

    @external
    def claim_project_tokens(self, caller: Address) -> U128:
        """Claim proportional allocation of launch project tokens after liquidity generation.

        Args:
            caller: Contributor address.
        """
        self._require_initialized()
        caller.require_auth()

        if not self.storage.get("liquidity_generated", False):
            raise ContractError.NOT_EXECUTED

        if self.storage.get(("claimed", caller), False):
            raise ContractError.ALREADY_CLAIMED

        user_contrib = self.storage.get(("contrib", caller), U128(0))
        if user_contrib == 0:
            raise ContractError.ZERO_AMOUNT

        self.storage.set(("claimed", caller), True)

        total_contrib = self.storage.get("total_stablecoin_contributed")
        total_rewards = self.storage.get("tokens_to_distribute")

        # reward = (user_contrib * total_rewards) / total_contrib
        reward_amount = (user_contrib * total_rewards) / total_contrib

        project_token = self.storage.get("project_token")
        self.env.invoke_contract(
            project_token,
            "transfer",
            [self.env.current_contract_address(), caller, reward_amount]
        )

        self.env.emit_event("tokens_claimed", {
            "user": caller,
            "reward": reward_amount,
        })

        return reward_amount

    @external
    def claim_vested_lp(self, caller: Address) -> U128:
        """Claim vested LP tokens to the project treasury.

        Args:
            caller: Account claiming the vested LP (must match treasury).
        """
        self._require_initialized()
        caller.require_auth()

        treasury = self.storage.get("treasury")
        if caller != treasury:
            raise ContractError.UNAUTHORIZED

        if not self.storage.get("liquidity_generated", False):
            raise ContractError.NOT_EXECUTED

        now = self.env.ledger().timestamp()
        vesting_start = self.storage.get("lp_vesting_start")
        vesting_duration = self.storage.get("lp_vesting_duration")
        total_lp = self.storage.get("total_lp_received")
        lp_withdrawn = self.storage.get("lp_withdrawn")

        if now <= vesting_start:
            raise ContractError.NOTHING_TO_VEST

        # Calculate vested amount
        elapsed = now - vesting_start
        if elapsed >= vesting_duration:
            vested_lp = total_lp
        else:
            vested_lp = (total_lp * U128(elapsed)) / U128(vesting_duration)

        claimable = vested_lp - lp_withdrawn
        if claimable == 0:
            raise ContractError.NOTHING_TO_VEST

        self.storage.set("lp_withdrawn", lp_withdrawn + claimable)

        lp_token = self.storage.get("lp_token")
        self.env.invoke_contract(
            lp_token,
            "transfer",
            [self.env.current_contract_address(), treasury, claimable]
        )

        self.env.emit_event("lp_tokens_vested_claimed", {
            "treasury": treasury,
            "claimed_amount": claimable,
            "total_withdrawn": lp_withdrawn + claimable,
        })

        return claimable

    @external
    def emergency_refund(self, caller: Address) -> U128:
        """Reclaim contributed stablecoins if LGE fails or is canceled.

        Args:
            caller: Contributor address.
        """
        self._require_initialized()
        caller.require_auth()

        is_canceled = self.storage.get("canceled", False)
        now = self.env.ledger().timestamp()
        contrib_end = self.storage.get("contrib_end")
        has_executed = self.storage.get("liquidity_generated", False)

        # LGE fails if: canceled, OR (past end and not executed after 7 days, OR past end with no contributions)
        # Let's verify failure conditions:
        is_failed = is_canceled or (now > (contrib_end + U64(604800)) and not has_executed)
        
        if not is_failed:
            raise ContractError.LGE_ACTIVE

        user_contrib = self.storage.get(("contrib", caller), U128(0))
        if user_contrib == 0:
            raise ContractError.ZERO_AMOUNT

        self.storage.set(("contrib", caller), U128(0))

        stablecoin = self.storage.get("stablecoin")
        self.env.invoke_contract(
            stablecoin,
            "transfer",
            [self.env.current_contract_address(), caller, user_contrib]
        )

        self.env.emit_event("emergency_refunded", {
            "user": caller,
            "refunded_amount": user_contrib,
        })

        return user_contrib

    @external
    def cancel_lge(self, admin: Address):
        """Cancel the LGE event before execution. (Admin only)

        Args:
            admin: Admin address.
        """
        self._require_initialized()
        admin.require_auth()
        self._require_admin(admin)

        if self.storage.get("liquidity_generated", False):
            raise ContractError.ALREADY_EXECUTED

        self.storage.set("canceled", True)
        self.env.emit_event("lge_canceled", {"admin": admin})

    @view
    def get_user_contribution(self, user: Address) -> U128:
        """Fetch the stablecoin contribution amount of a user.

        Args:
            user: User address.
        """
        return self.storage.get(("contrib", user), U128(0))

    @view
    def get_vesting_status(self) -> Map:
        """Get current LP token vesting status details."""
        res = Map()
        if not self.storage.get("liquidity_generated", False):
            res.set("vested", U128(0))
            res.set("claimable", U128(0))
            return res
        
        now = self.env.ledger().timestamp()
        vesting_start = self.storage.get("lp_vesting_start")
        vesting_duration = self.storage.get("lp_vesting_duration")
        total_lp = self.storage.get("total_lp_received")
        lp_withdrawn = self.storage.get("lp_withdrawn")

        elapsed = now - vesting_start
        if elapsed >= vesting_duration:
            vested = total_lp
        else:
            vested = (total_lp * U128(elapsed)) / U128(vesting_duration)

        res.set("vested", vested)
        res.set("claimable", vested - lp_withdrawn)
        return res

    @view
    def get_info(self) -> Map:
        """Retrieve LGE pool details and state."""
        res = Map()
        res.set("admin", self.storage.get("admin"))
        res.set("stablecoin", self.storage.get("stablecoin"))
        res.set("project_token", self.storage.get("project_token"))
        res.set("total_contributed", self.storage.get("total_stablecoin_contributed"))
        res.set("liquidity_generated", self.storage.get("liquidity_generated"))
        res.set("canceled", self.storage.get("canceled"))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED
