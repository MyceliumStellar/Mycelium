"""
Token Sale — Multi-round pricing tiers, cliff periods, admin withdrawal of sales proceeds.

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
    ROUND_NOT_ACTIVE = 4
    ROUND_EXPIRED = 5
    EXCEEDS_ROUND_SUPPLY = 6
    INSUFFICIENT_BALANCE = 7
    ZERO_AMOUNT = 8
    ROUND_NOT_FOUND = 9
    VESTING_NOT_STARTED = 10
    ALREADY_FINALIZED = 11


@contract
class TokenSale:
    """A multi-round token sale contract supporting separate price tiers, cliff durations, and vesting parameters."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, sale_token: Address, payment_token: Address):
        """Initialize the Token Sale contract.

        Args:
            admin: Admin address.
            sale_token: Token being sold.
            payment_token: Funding token (e.g. USDC).
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("sale_token", sale_token)
        self.storage.set("payment_token", payment_token)
        self.storage.set("round_counter", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "sale_token": sale_token,
        })

    @external
    def create_round(
        self,
        admin: Address,
        price_per_token: U128,  # Scale: payment tokens required per 1,000,000 sale tokens
        max_supply: U128,
        start_time: U64,
        duration: U64,
        tge_release_bps: U64,  # e.g. 1000 = 10% TGE unlock
        cliff_duration: U64,
        vesting_duration: U64,
    ) -> U64:
        """Create a new token sale round. Only admin.

        Args:
            admin: Admin address.
            price_per_token: Price of tokens in this round.
            max_supply: Total tokens allocated to this round.
            start_time: Round start timestamp.
            duration: Active phase duration in seconds.
            tge_release_bps: Percentage of tokens unlocked immediately upon sale end.
            cliff_duration: Cliff delay in seconds after round end.
            vesting_duration: Linear vesting duration in seconds.
        """
        self._require_initialized()
        self._require_admin(admin)

        if price_per_token == U128(0) or max_supply == U128(0):
            raise ContractError.ZERO_AMOUNT

        round_id = self.storage.get("round_counter") + U64(1)
        self.storage.set("round_counter", round_id)

        round_data = Map()
        round_data.set("id", round_id)
        round_data.set("price", price_per_token)
        round_data.set("max_supply", max_supply)
        round_data.set("total_sold", U128(0))
        round_data.set("start_time", start_time)
        round_data.set("end_time", start_time + duration)
        round_data.set("tge_release_bps", tge_release_bps)
        round_data.set("cliff_duration", cliff_duration)
        round_data.set("vesting_duration", vesting_duration)
        round_data.set("finalized", False)

        self.storage.set(("round", round_id), round_data)

        self.env.emit_event("round_created", {
            "round_id": round_id,
            "price": price_per_token,
            "max_supply": max_supply,
        })

        return round_id

    @external
    def buy_tokens(self, buyer: Address, round_id: U64, payment_amount: U128) -> U128:
        """Purchase tokens in a specific active sale round.

        Args:
            buyer: Buyer address.
            round_id: Round ID.
            payment_amount: Amount of payment tokens to spend.
        """
        self._require_initialized()
        buyer.require_auth()

        round_data = self.storage.get(("round", round_id), None)
        if round_data is None:
            raise ContractError.ROUND_NOT_FOUND

        now = self.env.ledger().timestamp()
        if now < round_data.get("start_time"):
            raise ContractError.ROUND_NOT_ACTIVE
        if now >= round_data.get("end_time"):
            raise ContractError.ROUND_EXPIRED

        if payment_amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        price = round_data.get("price")
        # Token amount to buy: payment_amount * 1,000,000 / price
        token_amount = (payment_amount * U128(1000000)) / price

        total_sold = round_data.get("total_sold")
        max_supply = round_data.get("max_supply")

        if total_sold + token_amount > max_supply:
            raise ContractError.EXCEEDS_ROUND_SUPPLY

        # Transfer payment tokens to contract
        payment_token = self.storage.get("payment_token")
        success = self.env.invoke_contract(
            payment_token,
            "transfer",
            [buyer, self.env.current_contract_address(), payment_amount]
        )
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        # Update round total sold
        round_data.set("total_sold", total_sold + token_amount)
        self.storage.set(("round", round_id), round_data)

        # Update buyer's purchased allocation for this round
        prev_purchased = self.storage.get(("user_purchase", buyer, round_id), U128(0))
        self.storage.set(("user_purchase", buyer, round_id), prev_purchased + token_amount)

        self.env.emit_event("tokens_purchased", {
            "round_id": round_id,
            "buyer": buyer,
            "spent": payment_amount,
            "bought": token_amount,
        })

        return token_amount

    @external
    def claim_tokens(self, user: Address, round_id: U64) -> U128:
        """Claim unlocked vested tokens for a specific round.

        Args:
            user: Purchaser reclaiming tokens.
            round_id: Target round ID.
        """
        self._require_initialized()
        user.require_auth()

        round_data = self.storage.get(("round", round_id), None)
        if round_data is None:
            raise ContractError.ROUND_NOT_FOUND

        now = self.env.ledger().timestamp()
        end_time = round_data.get("end_time")
        if now < end_time:
            raise ContractError.ROUND_NOT_ACTIVE

        purchased = self.storage.get(("user_purchase", user, round_id), U128(0))
        if purchased == U128(0):
            raise ContractError.ZERO_AMOUNT

        claimed = self.storage.get(("user_claimed", user, round_id), U128(0))

        # Calculate unlocked vested tokens
        unlocked = self._calculate_unlocked(round_data, purchased, now)
        claimable = unlocked - claimed

        if claimable == U128(0):
            raise ContractError.ZERO_AMOUNT

        self.storage.set(("user_claimed", user, round_id), claimed + claimable)

        # Transfer sale tokens to purchaser
        sale_token = self.storage.get("sale_token")
        self.env.invoke_contract(
            sale_token,
            "transfer",
            [self.env.current_contract_address(), user, claimable]
        )

        self.env.emit_event("tokens_claimed", {
            "round_id": round_id,
            "user": user,
            "amount": claimable,
        })

        return claimable

    @external
    def withdraw_proceeds(self, admin: Address, amount: U128) -> U128:
        """Withdraw sale contribution proceeds to the admin. Only admin.

        Args:
            admin: Admin address.
            amount: Payout amount.
        """
        self._require_initialized()
        self._require_admin(admin)

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        payment_token = self.storage.get("payment_token")
        
        # In a real environment, we'd check contract balance before transfer
        success = self.env.invoke_contract(
            payment_token,
            "transfer",
            [self.env.current_contract_address(), admin, amount]
        )
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        self.env.emit_event("proceeds_withdrawn", {"admin": admin, "amount": amount})

        return amount

    @external
    def reclaim_unsold_tokens(self, admin: Address, round_id: U64) -> U128:
        """Reclaim unsold allocation tokens after a round expires. Only admin.

        Args:
            admin: Admin address.
            round_id: Target round.
        """
        self._require_initialized()
        self._require_admin(admin)

        round_data = self.storage.get(("round", round_id), None)
        if round_data is None:
            raise ContractError.ROUND_NOT_FOUND

        now = self.env.ledger().timestamp()
        if now < round_data.get("end_time"):
            raise ContractError.ROUND_NOT_ACTIVE

        if round_data.get("finalized"):
            raise ContractError.ALREADY_FINALIZED

        round_data.set("finalized", True)
        self.storage.set(("round", round_id), round_data)

        max_supply = round_data.get("max_supply")
        sold = round_data.get("total_sold")
        unsold = max_supply - sold

        if unsold > U128(0):
            sale_token = self.storage.get("sale_token")
            self.env.invoke_contract(
                sale_token,
                "transfer",
                [self.env.current_contract_address(), admin, unsold]
            )

        self.env.emit_event("unsold_reclaimed", {"round_id": round_id, "amount": unsold})

        return unsold

    @view
    def get_claimable_tokens(self, user: Address, round_id: U64) -> U128:
        """View currently claimable vested tokens for a user in a round."""
        round_data = self.storage.get(("round", round_id), None)
        if round_data is None:
            return U128(0)

        purchased = self.storage.get(("user_purchase", user, round_id), U128(0))
        if purchased == U128(0):
            return U128(0)

        claimed = self.storage.get(("user_claimed", user, round_id), U128(0))
        now = self.env.ledger().timestamp()

        unlocked = self._calculate_unlocked(round_data, purchased, now)
        return unlocked - claimed

    @view
    def get_round_details(self, round_id: U64) -> Map:
        """Get details of a specific round."""
        return self.storage.get(("round", round_id))

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
        # General state helper
        pass

    def _calculate_unlocked(self, round_data: Map, total: U128, now: U64) -> U128:
        end_time = round_data.get("end_time")
        if now < end_time:
            return U128(0)

        tge_bps = round_data.get("tge_release_bps")
        cliff = round_data.get("cliff_duration")
        duration = round_data.get("vesting_duration")

        # TGE Release
        tge_release = (total * U128(tge_bps)) / U128(10000)

        if now < end_time + cliff:
            return tge_release

        vesting_start = end_time + cliff
        if now >= vesting_start + duration:
            return total

        elapsed = now - vesting_start
        vested_part = total - tge_release
        linear_unlocked = (vested_part * U128(elapsed)) / U128(duration)

        return tge_release + linear_unlocked
