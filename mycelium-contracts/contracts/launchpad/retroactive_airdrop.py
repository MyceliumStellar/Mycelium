"""
Retroactive Airdrop — User activity attestation logs, score calculations, reward claim windows, clawback to treasury of unclaimed.

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
    CLAIM_NOT_ACTIVE = 4
    CLAIM_EXPIRED = 5
    ALREADY_CLAIMED = 6
    ATTESTATION_CLOSED = 7
    NOT_EXPIRED = 8
    NO_REWARDS = 9
    ZERO_SCORE = 10
    ALREADY_CLAWED_BACK = 11
    INVALID_TIME_RANGE = 12
    ZERO_AMOUNT = 13

@contract
class RetroactiveAirdrop:
    """A retroactive airdrop contract verifying user scores via attestations and distributing rewards."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        reward_token: Address,
        treasury: Address,
        claim_start: U64,
        claim_end: U64,
        swap_multiplier: U128,
        lp_multiplier: U128,
        gov_multiplier: U128,
    ):
        """Initialize the retroactive airdrop contract.

        Args:
            admin: Admin address.
            reward_token: Token to be distributed as airdrop.
            treasury: Address where unclaimed tokens are clawed back.
            claim_start: Timestamp when users can start claiming.
            claim_end: Timestamp when claims end and clawback is enabled.
            swap_multiplier: Weight multiplier for swap volume.
            lp_multiplier: Weight multiplier for LP volume.
            gov_multiplier: Weight multiplier for governance votes count.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if claim_start >= claim_end:
            raise ContractError.INVALID_TIME_RANGE

        self.storage.set("admin", admin)
        self.storage.set("reward_token", reward_token)
        self.storage.set("treasury", treasury)
        self.storage.set("claim_start", claim_start)
        self.storage.set("claim_end", claim_end)
        
        self.storage.set("swap_multiplier", swap_multiplier)
        self.storage.set("lp_multiplier", lp_multiplier)
        self.storage.set("gov_multiplier", gov_multiplier)

        self.storage.set("total_score", U128(0))
        self.storage.set("total_rewards", U128(0))
        self.storage.set("clawed_back", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "reward_token": reward_token,
            "claim_start": claim_start,
            "claim_end": claim_end,
        })

    @external
    def fund_airdrop(self, admin: Address, amount: U128):
        """Deposit reward tokens into the airdrop pool. (Admin only)

        Args:
            admin: Admin address.
            amount: Launch token amount to fund.
        """
        self._require_initialized()
        admin.require_auth()
        self._require_admin(admin)

        if amount == 0:
            raise ContractError.ZERO_AMOUNT

        reward_token = self.storage.get("reward_token")
        self.env.invoke_contract(
            reward_token,
            "transfer",
            [admin, self.env.current_contract_address(), amount]
        )

        total_rewards = self.storage.get("total_rewards")
        self.storage.set("total_rewards", total_rewards + amount)

        self.env.emit_event("airdrop_funded", {"amount": amount})

    @external
    def update_multipliers(
        self,
        admin: Address,
        swap_mult: U128,
        lp_mult: U128,
        gov_mult: U128,
    ):
        """Update activity score multipliers before claim phase starts.

        Args:
            admin: Admin address.
            swap_mult: Swap multiplier.
            lp_mult: LP multiplier.
            gov_mult: Gov multiplier.
        """
        self._require_initialized()
        admin.require_auth()
        self._require_admin(admin)

        now = self.env.ledger().timestamp()
        claim_start = self.storage.get("claim_start")
        if now >= claim_start:
            raise ContractError.ATTESTATION_CLOSED

        self.storage.set("swap_multiplier", swap_mult)
        self.storage.set("lp_multiplier", lp_mult)
        self.storage.set("gov_multiplier", gov_mult)

        self.env.emit_event("multipliers_updated", {
            "swap_multiplier": swap_mult,
            "lp_multiplier": lp_mult,
            "gov_multiplier": gov_mult,
        })

    @external
    def attest_activities(self, admin: Address, attestations: Vec):
        """Attest user activity logs and calculate cumulative scores. (Admin/Oracle only)

        Args:
            admin: Admin address.
            attestations: Vec of Maps representing activity log items.
                         Map content: {"user": Address, "activity_type": U32, "value": U128}
                         activity_type: 0 for Swap, 1 for LP, 2 for Gov
        """
        self._require_initialized()
        admin.require_auth()
        self._require_admin(admin)

        now = self.env.ledger().timestamp()
        claim_start = self.storage.get("claim_start")
        if now >= claim_start:
            raise ContractError.ATTESTATION_CLOSED

        swap_mult = self.storage.get("swap_multiplier")
        lp_mult = self.storage.get("lp_multiplier")
        gov_mult = self.storage.get("gov_multiplier")
        total_score_added = U128(0)

        for i in range(len(attestations)):
            log = attestations.get(i)
            user = log.get("user")
            act_type = log.get("activity_type")
            value = log.get("value")

            multiplier = U128(0)
            if act_type == 0:
                multiplier = swap_mult
            elif act_type == 1:
                multiplier = lp_mult
            elif act_type == 2:
                multiplier = gov_mult
            else:
                continue

            score_increment = value * multiplier
            if score_increment > 0:
                user_score = self.storage.get(("score", user), U128(0))
                self.storage.set(("score", user), user_score + score_increment)
                total_score_added += score_increment

        if total_score_added > 0:
            total_score = self.storage.get("total_score")
            self.storage.set("total_score", total_score + total_score_added)

        self.env.emit_event("activities_attested", {
            "count": len(attestations),
            "score_added": total_score_added,
        })

    @external
    def claim(self, caller: Address) -> U128:
        """Claim airdropped tokens based on score and total rewards.

        Args:
            caller: Account claiming the airdrop rewards.
        """
        self._require_initialized()
        caller.require_auth()

        now = self.env.ledger().timestamp()
        claim_start = self.storage.get("claim_start")
        claim_end = self.storage.get("claim_end")

        if now < claim_start:
            raise ContractError.CLAIM_NOT_ACTIVE
        if now >= claim_end:
            raise ContractError.CLAIM_EXPIRED

        if self.storage.get(("claimed", caller), False):
            raise ContractError.ALREADY_CLAIMED

        user_score = self.storage.get(("score", caller), U128(0))
        if user_score == 0:
            raise ContractError.ZERO_SCORE

        total_score = self.storage.get("total_score")
        if total_score == 0:
            raise ContractError.ZERO_SCORE

        total_rewards = self.storage.get("total_rewards")
        
        # Proportional reward: (user_score * total_rewards) / total_score
        reward_amount = (user_score * total_rewards) / total_score

        if reward_amount == 0:
            raise ContractError.NO_REWARDS

        self.storage.set(("claimed", caller), True)

        reward_token = self.storage.get("reward_token")
        self.env.invoke_contract(
            reward_token,
            "transfer",
            [self.env.current_contract_address(), caller, reward_amount]
        )

        self.env.emit_event("airdrop_claimed", {
            "claimant": caller,
            "score": user_score,
            "amount": reward_amount,
        })

        return reward_amount

    @external
    def clawback(self, admin: Address) -> U128:
        """Clawback unclaimed tokens to the treasury after the claim window ends.

        Args:
            admin: Admin address.
        """
        self._require_initialized()
        admin.require_auth()
        self._require_admin(admin)

        now = self.env.ledger().timestamp()
        claim_end = self.storage.get("claim_end")
        if now < claim_end:
            raise ContractError.NOT_EXPIRED

        if self.storage.get("clawed_back", False):
            raise ContractError.ALREADY_CLAWED_BACK

        self.storage.set("clawed_back", True)

        reward_token = self.storage.get("reward_token")
        
        # Get remaining contract balance
        remaining_balance = self.env.invoke_contract(
            reward_token,
            "balance",
            [self.env.current_contract_address()]
        )

        if remaining_balance > 0:
            treasury = self.storage.get("treasury")
            self.env.invoke_contract(
                reward_token,
                "transfer",
                [self.env.current_contract_address(), treasury, remaining_balance]
            )

        self.env.emit_event("unclaimed_clawed_back", {
            "treasury": self.storage.get("treasury"),
            "amount": remaining_balance,
        })

        return remaining_balance

    @view
    def get_user_score(self, user: Address) -> U128:
        """Get the cumulative activity score of a user.

        Args:
            user: User address.
        """
        return self.storage.get(("score", user), U128(0))

    @view
    def estimate_reward(self, user: Address) -> U128:
        """Estimate the launch token reward for a user based on their current score.

        Args:
            user: User address.
        """
        user_score = self.storage.get(("score", user), U128(0))
        if user_score == 0:
            return U128(0)

        total_score = self.storage.get("total_score")
        if total_score == 0:
            return U128(0)

        total_rewards = self.storage.get("total_rewards")
        return (user_score * total_rewards) / total_score

    @view
    def is_claimed(self, user: Address) -> Bool:
        """Check if a user has claimed their airdrop.

        Args:
            user: User address.
        """
        return self.storage.get(("claimed", user), False)

    @view
    def get_info(self) -> Map:
        """Retrieve info about the retroactive airdrop."""
        res = Map()
        res.set("admin", self.storage.get("admin"))
        res.set("reward_token", self.storage.get("reward_token"))
        res.set("treasury", self.storage.get("treasury"))
        res.set("claim_start", self.storage.get("claim_start"))
        res.set("claim_end", self.storage.get("claim_end"))
        res.set("total_score", self.storage.get("total_score"))
        res.set("total_rewards", self.storage.get("total_rewards"))
        res.set("clawed_back", self.storage.get("clawed_back"))
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
