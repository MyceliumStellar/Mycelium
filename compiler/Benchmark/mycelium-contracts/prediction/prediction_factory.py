"""
Prediction Factory — Deploy market instances, category templates, fee distributions, administrative limits.

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
    INVALID_WASM_HASH = 4
    TOKEN_NOT_ALLOWED = 5
    ORACLE_NOT_ALLOWED = 6
    INVALID_FEE_SPLIT = 7
    MARKET_NOT_FOUND = 8


@contract
class PredictionFactory:
    """A factory contract for deploying, registering, and managing prediction market instances."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        fee_receiver: Address,
        binary_wasm: Bytes,
        scalar_wasm: Bytes,
        categorical_wasm: Bytes,
    ):
        """Initialize the factory.

        Args:
            admin: Administrative account.
            fee_receiver: Vault or recipient for protocol fees.
            binary_wasm: Wasm hash of BinaryPrediction contract.
            scalar_wasm: Wasm hash of ScalarPrediction contract.
            categorical_wasm: Wasm hash of CategoricalPrediction contract.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("fee_receiver", fee_receiver)
        self.storage.set("binary_wasm", binary_wasm)
        self.storage.set("scalar_wasm", scalar_wasm)
        self.storage.set("categorical_wasm", categorical_wasm)

        self.storage.set("market_count", U64(0))
        self.storage.set("factory_fee_bps", U64(100)) # 1% default trade fee for deployed markets
        self.storage.set("creator_share_bps", U64(2000)) # 20% of trading fees go to market creator

        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "fee_receiver": fee_receiver,
        })

    @external
    def deploy_binary_market(
        self,
        creator: Address,
        collateral_token: Address,
        oracle: Address,
        salt: Bytes,
    ) -> Address:
        """Deploy a new YES/NO prediction market instance.

        Args:
            creator: Address of the market creator.
            collateral_token: Token to use for prediction pool collateral.
            oracle: Resolution oracle address.
            salt: Unique deployment salt.
        """
        self._require_initialized()
        creator.require_auth()

        self._check_token_and_oracle(collateral_token, oracle)

        wasm_hash = self.storage.get("binary_wasm")
        if len(wasm_hash) == 0:
            raise ContractError.INVALID_WASM_HASH

        # Deploy instance via Soroban Deployer
        deployed_address = self.env.deployer().with_current_contract(salt).deploy(wasm_hash)

        # Initialize the deployed contract
        # BinaryPrediction.initialize(admin, collateral_token, oracle, fee_bps)
        # We pass factory as admin to retain control, or creator
        fee_bps = self.storage.get("factory_fee_bps")
        self.env.invoke_contract(
            deployed_address,
            "initialize",
            [self.env.current_contract_address(), collateral_token, oracle, fee_bps]
        )

        market_id = self.storage.get("market_count") + U64(1)
        self.storage.set("market_count", market_id)

        market_info = Map()
        market_info.set("id", market_id)
        market_info.set("type", Symbol("binary"))
        market_info.set("address", deployed_address)
        market_info.set("creator", creator)
        market_info.set("token", collateral_token)
        market_info.set("oracle", oracle)

        self.storage.set(("market", deployed_address), market_info)
        self.storage.set(("market_by_id", market_id), deployed_address)

        self.env.emit_event("market_deployed", {
            "market_id": market_id,
            "type": Symbol("binary"),
            "address": deployed_address,
            "creator": creator,
        })

        return deployed_address

    @external
    def deploy_scalar_market(
        self,
        creator: Address,
        collateral_token: Address,
        oracle: Address,
        lower_bound: I128,
        upper_bound: I128,
        salt: Bytes,
    ) -> Address:
        """Deploy a new scalar prediction market instance.

        Args:
            creator: Market creator.
            collateral_token: Collateral token.
            oracle: Resolution oracle.
            lower_bound: Minimum value of prediction.
            upper_bound: Maximum value of prediction.
            salt: Deployment salt.
        """
        self._require_initialized()
        creator.require_auth()

        self._check_token_and_oracle(collateral_token, oracle)

        wasm_hash = self.storage.get("scalar_wasm")
        if len(wasm_hash) == 0:
            raise ContractError.INVALID_WASM_HASH

        deployed_address = self.env.deployer().with_current_contract(salt).deploy(wasm_hash)

        # Initialize the deployed scalar market
        fee_bps = self.storage.get("factory_fee_bps")
        self.env.invoke_contract(
            deployed_address,
            "initialize",
            [self.env.current_contract_address(), collateral_token, oracle, lower_bound, upper_bound, fee_bps]
        )

        market_id = self.storage.get("market_count") + U64(1)
        self.storage.set("market_count", market_id)

        market_info = Map()
        market_info.set("id", market_id)
        market_info.set("type", Symbol("scalar"))
        market_info.set("address", deployed_address)
        market_info.set("creator", creator)
        market_info.set("token", collateral_token)
        market_info.set("oracle", oracle)

        self.storage.set(("market", deployed_address), market_info)
        self.storage.set(("market_by_id", market_id), deployed_address)

        self.env.emit_event("market_deployed", {
            "market_id": market_id,
            "type": Symbol("scalar"),
            "address": deployed_address,
            "creator": creator,
        })

        return deployed_address

    @external
    def distribute_market_fees(self, caller: Address, market: Address) -> U128:
        """Claim trading fees from a deployed market and distribute them between factory and creator.

        Args:
            caller: Trigger address.
            market: Deployed market address.
        """
        self._require_initialized()
        market_info = self.storage.get(("market", market), None)
        if market_info is None:
            raise ContractError.MARKET_NOT_FOUND

        creator = market_info.get("creator")
        token = market_info.get("token")

        # Invoke withdraw_fees on the child market to this factory
        fees_collected = self.env.invoke_contract(market, "withdraw_fees", [self.env.current_contract_address()])
        if fees_collected == U128(0):
            return U128(0)

        # Distribute based on creator_share_bps
        creator_share_bps = self.storage.get("creator_share_bps")
        creator_payout = (fees_collected * U128(creator_share_bps)) / U128(10000)
        factory_payout = fees_collected - creator_payout

        fee_receiver = self.storage.get("fee_receiver")

        if creator_payout > U128(0):
            self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), creator, creator_payout])
        if factory_payout > U128(0):
            self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), fee_receiver, factory_payout])

        self.env.emit_event("fees_distributed", {
            "market": market,
            "total_fees": fees_collected,
            "creator_payout": creator_payout,
            "factory_payout": factory_payout,
        })

        return fees_collected

    @external
    def set_whitelists(
        self,
        caller: Address,
        target: Address,
        is_token: Bool,
        allowed: Bool,
    ):
        """Whitelist allowed collateral tokens and resolution oracles. Only admin.

        Args:
            caller: Admin address.
            target: Token or Oracle address to whitelist.
            is_token: True if target is token, False if oracle.
            allowed: True to authorize, False to remove.
        """
        self._require_initialized()
        self._require_admin(caller)

        if is_token:
            self.storage.set(("whitelisted_token", target), allowed)
        else:
            self.storage.set(("whitelisted_oracle", target), allowed)

        self.env.emit_event("whitelist_updated", {
            "target": target,
            "is_token": is_token,
            "allowed": allowed,
        })

    @external
    def update_wasm_hashes(
        self,
        caller: Address,
        binary_wasm: Bytes,
        scalar_wasm: Bytes,
        categorical_wasm: Bytes,
    ):
        """Update WASM hashes for markets. Only admin.

        Args:
            caller: Admin address.
            binary_wasm: Binary Prediction WASM.
            scalar_wasm: Scalar Prediction WASM.
            categorical_wasm: Categorical Prediction WASM.
        """
        self._require_initialized()
        self._require_admin(caller)

        self.storage.set("binary_wasm", binary_wasm)
        self.storage.set("scalar_wasm", scalar_wasm)
        self.storage.set("categorical_wasm", categorical_wasm)

        self.env.emit_event("wasm_hashes_updated", {})

    @external
    def update_fee_rules(
        self,
        caller: Address,
        factory_fee_bps: U64,
        creator_share_bps: U64,
    ):
        """Update factory transaction fee rules. Only admin.

        Args:
            caller: Admin address.
            factory_fee_bps: Default trade fee bps for new markets.
            creator_share_bps: Share of fees distributed to creators (bps).
        """
        self._require_initialized()
        self._require_admin(caller)

        if creator_share_bps > U64(10000):
            raise ContractError.INVALID_FEE_SPLIT

        self.storage.set("factory_fee_bps", factory_fee_bps)
        self.storage.set("creator_share_bps", creator_share_bps)

        self.env.emit_event("fee_rules_updated", {
            "factory_fee_bps": factory_fee_bps,
            "creator_share_bps": creator_share_bps,
        })

    @view
    def get_market_info(self, market: Address) -> Map:
        """Get info of deployed market."""
        return self.storage.get(("market", market))

    @view
    def is_valid_market(self, market: Address) -> Bool:
        """Check if market was deployed by this factory."""
        return self.storage.get(("market", market)) is not None

    @view
    def get_market_count(self) -> U64:
        """Get count of deployed markets."""
        return self.storage.get("market_count", U64(0))

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

    def _check_token_and_oracle(self, token: Address, oracle: Address):
        if not self.storage.get(("whitelisted_token", token), False):
            raise ContractError.TOKEN_NOT_ALLOWED
        if not self.storage.get(("whitelisted_oracle", oracle), False):
            raise ContractError.ORACLE_NOT_ALLOWED
