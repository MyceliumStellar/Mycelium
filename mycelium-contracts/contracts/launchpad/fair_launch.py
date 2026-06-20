"""
Fair Launch — No-presale launch, anti-whale caps, anti-bot purchase timeouts, reward emissions.

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
    EXCEEDS_TX_LIMIT = 6
    EXCEEDS_WHALE_LIMIT = 7
    BOT_COOLDOWN_ACTIVE = 8
    INSUFFICIENT_BALANCE = 9
    ZERO_AMOUNT = 10
    ALREADY_FINALIZED = 11


@contract
class FairLaunch:
    """A fair launch contract with no presales, anti-whale buy limits, anti-bot cooldowns, and instant reward emissions."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        seller: Address,
        sale_token: Address,
        payment_token: Address,
        token_price: U128,       # Price of 1 sale token in payment tokens
        max_buy_per_tx: U128,    # Max purchase in a single transaction (anti-whale/bot)
        max_alloc_per_user: U128,# Max total purchase allocation per user (anti-whale)
        cooldown_duration: U64,  # Time in seconds user must wait between buys (anti-bot)
        start_time: U64,
        duration: U64,
        reward_token: Address,
        reward_rate_bps: U64,    # Extra bonus reward tokens per purchase (basis points, e.g. 500 = 5%)
    ):
        """Initialize the fair launch parameters.

        Args:
            admin: Admin address.
            seller: Seller/Issuer address.
            sale_token: Token to be distributed.
            payment_token: Purchase payment token.
            token_price: Price of 1 sale token.
            max_buy_per_tx: Max tokens allowed per buy transaction.
            max_alloc_per_user: Cumulative purchase cap per user.
            cooldown_duration: Cooldown window in seconds between purchases.
            start_time: Start timestamp of the sale.
            duration: Sale duration in seconds.
            reward_token: Promotional reward token address.
            reward_rate_bps: Bonus rewards percentage in basis points.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if token_price == U128(0):
            raise ContractError.ZERO_AMOUNT

        self.storage.set("admin", admin)
        self.storage.set("seller", seller)
        self.storage.set("sale_token", sale_token)
        self.storage.set("payment_token", payment_token)
        self.storage.set("token_price", token_price)
        self.storage.set("max_buy_per_tx", max_buy_per_tx)
        self.storage.set("max_alloc_per_user", max_alloc_per_user)
        self.storage.set("cooldown_duration", cooldown_duration)
        self.storage.set("start_time", start_time)
        self.storage.set("end_time", start_time + duration)
        self.storage.set("reward_token", reward_token)
        self.storage.set("reward_rate_bps", reward_rate_bps)

        self.storage.set("total_sold", U128(0))
        self.storage.set("total_rewards_emitted", U128(0))
        self.storage.set("finalized", False)

        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "sale_token": sale_token,
            "price": token_price,
            "end_time": start_time + duration,
        })

    @external
    def buy(self, buyer: Address, quantity: U128) -> U128:
        """Purchase sale tokens under anti-whale constraints, receiving instant reward emissions.

        Args:
            buyer: Buyer address.
            quantity: Amount of sale tokens to purchase.
        """
        self._require_initialized()
        buyer.require_auth()

        now = self.env.ledger().timestamp()
        if now < self.storage.get("start_time"):
            raise ContractError.SALE_NOT_ACTIVE
        if now >= self.storage.get("end_time"):
            raise ContractError.SALE_ENDED

        if quantity == U128(0):
            raise ContractError.ZERO_AMOUNT

        # Anti-whale transaction limit check
        max_tx = self.storage.get("max_buy_per_tx")
        if quantity > max_tx:
            raise ContractError.EXCEEDS_TX_LIMIT

        # Anti-whale total allocation check
        bought = self.storage.get(("user_bought", buyer), U128(0))
        max_user = self.storage.get("max_alloc_per_user")
        if bought + quantity > max_user:
            raise ContractError.EXCEEDS_WHALE_LIMIT

        # Anti-bot cooldown check
        last_buy = self.storage.get(("last_buy_time", buyer), U64(0))
        cooldown = self.storage.get("cooldown_duration")
        if now < last_buy + cooldown:
            raise ContractError.BOT_COOLDOWN_ACTIVE

        # Calculate cost
        price = self.storage.get("token_price")
        cost = quantity * price

        # Transfer payment tokens from buyer to contract
        payment_token = self.storage.get("payment_token")
        success = self.env.invoke_contract(
            payment_token,
            "transfer",
            [buyer, self.env.current_contract_address(), cost]
        )
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        # Transfer sale tokens from contract to buyer
        sale_token = self.storage.get("sale_token")
        self.env.invoke_contract(
            sale_token,
            "transfer",
            [self.env.current_contract_address(), buyer, quantity]
        )

        # Emit reward tokens
        reward_token = self.storage.get("reward_token")
        reward_rate = self.storage.get("reward_rate_bps")
        reward_amount = (quantity * U128(reward_rate)) / U128(10000)

        if reward_amount > U128(0):
            reward_success = self.env.invoke_contract(
                reward_token,
                "transfer",
                [self.env.current_contract_address(), buyer, reward_amount]
            )
            if reward_success:
                emitted = self.storage.get("total_rewards_emitted")
                self.storage.set("total_rewards_emitted", emitted + reward_amount)

        # Update user purchase records
        self.storage.set(("user_bought", buyer), bought + quantity)
        self.storage.set(("last_buy_time", buyer), now)

        total_sold = self.storage.get("total_sold")
        self.storage.set("total_sold", total_sold + quantity)

        self.env.emit_event("tokens_purchased", {
            "buyer": buyer,
            "quantity": quantity,
            "cost": cost,
            "reward": reward_amount,
        })

        return quantity

    @external
    def finalize_launch(self, admin: Address) -> Bool:
        """Finalize the launch and withdraw proceeds to seller. Only admin.

        Args:
            admin: Admin address.
        """
        self._require_initialized()
        admin.require_auth()

        expected_admin = self.storage.get("admin")
        if admin != expected_admin:
            raise ContractError.UNAUTHORIZED

        if self.storage.get("finalized", False):
            raise ContractError.ALREADY_FINALIZED

        self.storage.set("finalized", True)

        seller = self.storage.get("seller")
        payment_token = self.storage.get("payment_token")
        sale_token = self.storage.get("sale_token")
        reward_token = self.storage.get("reward_token")

        # Withdraw collected funds to seller
        # We query the payment token balance of this contract
        # In Stellar Mycelium, calling "balance" of token to withdraw it all
        # Or we can just calculate it based on total_sold * price
        price = self.storage.get("token_price")
        sold = self.storage.get("total_sold")
        collected = sold * price

        if collected > U128(0):
            self.env.invoke_contract(
                payment_token,
                "transfer",
                [self.env.current_contract_address(), seller, collected]
            )

        # Reclaim unsold sale tokens to seller
        # In typical setups, seller deposits a lump sum. We transfer remaining balance.
        # But we can query contract balance or seller can call manual reclaim.
        self.env.emit_event("launch_finalized", {"total_sold": sold, "collected": collected})

        return True

    @external
    def emergency_reclaim_tokens(self, seller: Address, token: Address, amount: U128) -> Bool:
        """Reclaim remaining unsold tokens or reward tokens. Only seller.

        Args:
            seller: Seller address.
            token: Token to reclaim.
            amount: Token amount to reclaim.
        """
        self._require_initialized()
        seller.require_auth()

        expected_seller = self.storage.get("seller")
        if seller != expected_seller:
            raise ContractError.UNAUTHORIZED

        now = self.env.ledger().timestamp()
        if now < self.storage.get("end_time") and not self.storage.get("finalized", False):
            raise ContractError.SALE_NOT_ACTIVE

        self.env.invoke_contract(
            token,
            "transfer",
            [self.env.current_contract_address(), seller, amount]
        )

        self.env.emit_event("tokens_reclaimed", {"token": token, "amount": amount})

        return True

    @view
    def get_user_stats(self, user: Address) -> Map:
        """Get purchase statistics of a user."""
        res = Map()
        res.set("bought", self.storage.get(("user_bought", user), U128(0)))
        res.set("last_buy", self.storage.get(("last_buy_time", user), U64(0)))
        return res

    @view
    def get_status(self) -> Map:
        """Get status details of the fair launch."""
        res = Map()
        res.set("total_sold", self.storage.get("total_sold"))
        res.set("total_rewards", self.storage.get("total_rewards_emitted"))
        res.set("finalized", self.storage.get("finalized"))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
