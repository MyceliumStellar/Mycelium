"""
Futarchy Governance — Prediction market governance with YES/NO conditional pools, AMMs, and collateral redemptions.

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
    PROPOSAL_NOT_FOUND = 4
    TRADING_PERIOD_ACTIVE = 5
    TRADING_PERIOD_ENDED = 6
    INSUFFICIENT_FUNDS = 7
    INSUFFICIENT_TOKENS = 8
    PROPOSAL_ALREADY_RESOLVED = 9
    PROPOSAL_NOT_RESOLVED = 10
    ZERO_AMOUNT = 11
    SLIPPAGE_EXCEEDED = 12


class ProposalState:
    TRADING = 0
    PASSED = 1
    FAILED = 2
    EXECUTED = 3


@contract
class FutarchyGovernance:
    """Prediction market governance contract driving decisions via conditional market token trading."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        collateral_token: Address,
        trading_duration: U64,
        market_subsidy: U128,
    ):
        """Initialize the Futarchy Governance contract.

        Args:
            admin: Admin address.
            collateral_token: Collateral token address (e.g. USDC).
            trading_duration: Time conditional markets remain open.
            market_subsidy: Amount of collateral the DAO puts into initial YES/NO liquidity pools.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        if market_subsidy == U128(0):
            raise ContractError.ZERO_AMOUNT

        self.storage.set("admin", admin)
        self.storage.set("collateral_token", collateral_token)
        self.storage.set("trading_duration", trading_duration)
        self.storage.set("market_subsidy", market_subsidy)
        self.storage.set("proposal_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "collateral_token": collateral_token,
            "market_subsidy": market_subsidy,
        })

    @external
    def propose_action(
        self,
        proposer: Address,
        target: Address,
        calldata: Bytes,
        description: Symbol,
    ) -> U64:
        """Propose an action, deploying YES and NO conditional market AMM pools with dynamic initial liquidity.

        Args:
            proposer: Proposer address.
            target: Execution target.
            calldata: Execution calldata.
            description: Description symbol.
        """
        self._require_initialized()
        proposer.require_auth()

        # Deduct subsidy from contract's collateral balance (must be pre-funded or top up)
        token = self.storage.get("collateral_token")
        subsidy = self.storage.get("market_subsidy")
        cash = self.env.invoke_contract(token, "balance", [self.env.current_contract_address()])

        # Ensure we have enough collateral to fund the YES and NO pools (2 * subsidy)
        required_funding = subsidy * U128(2)
        if cash < required_funding:
            raise ContractError.INSUFFICIENT_FUNDS

        proposal_id = self.storage.get("proposal_count") + U64(1)
        self.storage.set("proposal_count", proposal_id)

        now = self.env.ledger().timestamp()
        trading_end = now + self.storage.get("trading_duration")

        proposal = {
            "id": proposal_id,
            "proposer": proposer,
            "target": target,
            "calldata": calldata,
            "description": description,
            "trading_end": trading_end,
            "state": ProposalState.TRADING,
        }

        self.storage.set(("proposal", proposal_id), proposal)

        # Seed YES and NO AMM pools
        # Pool Collateral = subsidy, Pool Tokens = subsidy
        self.storage.set(("yes_pool_collateral", proposal_id), subsidy)
        self.storage.set(("yes_pool_tokens", proposal_id), subsidy)

        self.storage.set(("no_pool_collateral", proposal_id), subsidy)
        self.storage.set(("no_pool_tokens", proposal_id), subsidy)

        self.env.emit_event("proposal_created", {
            "proposal_id": proposal_id,
            "proposer": proposer,
            "trading_end": trading_end,
        })

        return proposal_id

    @external
    def mint_conditional_tokens(self, trader: Address, proposal_id: U64, amount: U128):
        """Deposit collateral to mint equal amounts of YES and NO conditional tokens.

        Args:
            trader: Trading address.
            proposal_id: Target proposal ID.
            amount: Collateral amount.
        """
        self._require_initialized()
        trader.require_auth()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        proposal = self._get_proposal(proposal_id)
        if proposal["state"] != ProposalState.TRADING:
            raise ContractError.PROPOSAL_ALREADY_RESOLVED

        # Escrow collateral from trader
        token = self.storage.get("collateral_token")
        success = self.env.invoke_contract(token, "transfer", [trader, self.env.current_contract_address(), amount])
        if not success:
            raise ContractError.INSUFFICIENT_FUNDS

        # Mint YES and NO tokens
        yes_bal = self.storage.get(("yes_balance", proposal_id, trader), U128(0))
        no_bal = self.storage.get(("no_balance", proposal_id, trader), U128(0))

        self.storage.set(("yes_balance", proposal_id, trader), yes_bal + amount)
        self.storage.set(("no_balance", proposal_id, trader), no_bal + amount)

        self.env.emit_event("conditional_tokens_minted", {
            "proposal_id": proposal_id,
            "trader": trader,
            "amount": amount,
        })

    @external
    def merge_conditional_tokens(self, trader: Address, proposal_id: U64, amount: U128):
        """Redeem collateral by burning equal amounts of YES and NO tokens (anytime).

        Args:
            trader: Trading address.
            proposal_id: Proposal ID.
            amount: Amount of matched pairs to burn.
        """
        self._require_initialized()
        trader.require_auth()

        yes_bal = self.storage.get(("yes_balance", proposal_id, trader), U128(0))
        no_bal = self.storage.get(("no_balance", proposal_id, trader), U128(0))

        if yes_bal < amount or no_bal < amount:
            raise ContractError.INSUFFICIENT_TOKENS

        self.storage.set(("yes_balance", proposal_id, trader), yes_bal - amount)
        self.storage.set(("no_balance", proposal_id, trader), no_bal - amount)

        # Refund collateral
        token = self.storage.get("collateral_token")
        self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), trader, amount])

        self.env.emit_event("conditional_tokens_merged", {
            "proposal_id": proposal_id,
            "trader": trader,
            "amount": amount,
        })

    @external
    def swap_collateral_for_yes(
        self,
        trader: Address,
        proposal_id: U64,
        collateral_in: U128,
        min_yes_out: U128,
    ) -> U128:
        """Buy YES conditional tokens using collateral via AMM constant product pool.

        Trader deposits collateral, receives YES tokens.
        """
        self._require_initialized()
        trader.require_auth()

        proposal = self._get_proposal(proposal_id)
        if proposal["state"] != ProposalState.TRADING:
            raise ContractError.PROPOSAL_ALREADY_RESOLVED

        now = self.env.ledger().timestamp()
        if now >= proposal["trading_end"]:
            raise ContractError.TRADING_PERIOD_ENDED

        # Swap math: yes_out = (yes_pool_tokens * collateral_in) / (yes_pool_collateral + collateral_in)
        yes_collateral = self.storage.get(("yes_pool_collateral", proposal_id))
        yes_tokens = self.storage.get(("yes_pool_tokens", proposal_id))

        yes_out = (yes_tokens * collateral_in) / (yes_collateral + collateral_in)
        if yes_out < min_yes_out:
            raise ContractError.SLIPPAGE_EXCEEDED

        # Escrow collateral from trader
        token = self.storage.get("collateral_token")
        success = self.env.invoke_contract(token, "transfer", [trader, self.env.current_contract_address(), collateral_in])
        if not success:
            raise ContractError.INSUFFICIENT_FUNDS

        # Update pool state
        self.storage.set(("yes_pool_collateral", proposal_id), yes_collateral + collateral_in)
        self.storage.set(("yes_pool_tokens", proposal_id), yes_tokens - yes_out)

        # Credit trader YES balance
        trader_yes = self.storage.get(("yes_balance", proposal_id, trader), U128(0))
        self.storage.set(("yes_balance", proposal_id, trader), trader_yes + yes_out)

        self.env.emit_event("swapped_yes", {
            "proposal_id": proposal_id,
            "trader": trader,
            "collateral_in": collateral_in,
            "yes_out": yes_out,
        })

        return yes_out

    @external
    def swap_collateral_for_no(
        self,
        trader: Address,
        proposal_id: U64,
        collateral_in: U128,
        min_no_out: U128,
    ) -> U128:
        """Buy NO conditional tokens using collateral via AMM constant product pool.

        Trader deposits collateral, receives NO tokens.
        """
        self._require_initialized()
        trader.require_auth()

        proposal = self._get_proposal(proposal_id)
        if proposal["state"] != ProposalState.TRADING:
            raise ContractError.PROPOSAL_ALREADY_RESOLVED

        now = self.env.ledger().timestamp()
        if now >= proposal["trading_end"]:
            raise ContractError.TRADING_PERIOD_ENDED

        # Swap math: no_out = (no_pool_tokens * collateral_in) / (no_pool_collateral + collateral_in)
        no_collateral = self.storage.get(("no_pool_collateral", proposal_id))
        no_tokens = self.storage.get(("no_pool_tokens", proposal_id))

        no_out = (no_tokens * collateral_in) / (no_collateral + collateral_in)
        if no_out < min_no_out:
            raise ContractError.SLIPPAGE_EXCEEDED

        # Escrow collateral from trader
        token = self.storage.get("collateral_token")
        success = self.env.invoke_contract(token, "transfer", [trader, self.env.current_contract_address(), collateral_in])
        if not success:
            raise ContractError.INSUFFICIENT_FUNDS

        # Update pool state
        self.storage.set(("no_pool_collateral", proposal_id), no_collateral + collateral_in)
        self.storage.set(("no_pool_tokens", proposal_id), no_tokens - no_out)

        # Credit trader NO balance
        trader_no = self.storage.get(("no_balance", proposal_id, trader), U128(0))
        self.storage.set(("no_balance", proposal_id, trader), trader_no + no_out)

        self.env.emit_event("swapped_no", {
            "proposal_id": proposal_id,
            "trader": trader,
            "collateral_in": collateral_in,
            "no_out": no_out,
        })

        return no_out

    @external
    def resolve_proposal(self, caller: Address, proposal_id: U64):
        """Evaluate the price of YES vs NO conditional pools. Passes if YES price is higher.

        Price = Collateral in pool / Tokens in pool.
        Higher price = YES passed, NO failed.

        Args:
            caller: Trigger address.
            proposal_id: Proposal ID.
        """
        self._require_initialized()
        caller.require_auth()

        proposal = self._get_proposal(proposal_id)
        if proposal["state"] != ProposalState.TRADING:
            raise ContractError.PROPOSAL_ALREADY_RESOLVED

        now = self.env.ledger().timestamp()
        if now < proposal["trading_end"]:
            raise ContractError.TRADING_PERIOD_ACTIVE

        # Calculate implied prices (scaled by 1e6 to avoid float limitations)
        yes_col = self.storage.get(("yes_pool_collateral", proposal_id))
        yes_tok = self.storage.get(("yes_pool_tokens", proposal_id))

        no_col = self.storage.get(("no_pool_collateral", proposal_id))
        no_tok = self.storage.get(("no_pool_tokens", proposal_id))

        yes_price = (yes_col * U128(1000000)) / yes_tok
        no_price = (no_col * U128(1000000)) / no_tok

        # Decision rule: YES price > NO price (with optional margin)
        if yes_price > no_price:
            proposal["state"] = ProposalState.PASSED
        else:
            proposal["state"] = ProposalState.FAILED

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("proposal_resolved", {
            "proposal_id": proposal_id,
            "state": proposal["state"],
            "yes_price": yes_price,
            "no_price": no_price,
        })

    @external
    def execute_passed_action(self, executor: Address, proposal_id: U64):
        """Execute proposal if resolved as PASSED.

        Args:
            executor: Trigger address.
            proposal_id: Proposal ID.
        """
        self._require_initialized()
        executor.require_auth()

        proposal = self._get_proposal(proposal_id)
        if proposal["state"] != ProposalState.PASSED:
            raise ContractError.PROPOSAL_NOT_RESOLVED

        proposal["state"] = ProposalState.EXECUTED
        self.storage.set(("proposal", proposal_id), proposal)

        success = self.env.invoke_contract(proposal["target"], "execute", [proposal["calldata"]])
        if not success:
            proposal["state"] = ProposalState.PASSED
            self.storage.set(("proposal", proposal_id), proposal)
            raise ContractError.PROPOSAL_NOT_RESOLVED

        self.env.emit_event("proposal_executed", {
            "proposal_id": proposal_id,
            "executor": executor,
        })

    @external
    def redeem_winning_tokens(self, trader: Address, proposal_id: U64, token_amount: U128):
        """Redeem collateral 1-to-1 if holding the winning token of a resolved proposal.

        Losing tokens are worth 0.

        Args:
            trader: Trading address.
            proposal_id: Proposal ID.
            token_amount: Winning tokens to burn.
        """
        self._require_initialized()
        trader.require_auth()

        proposal = self._get_proposal(proposal_id)
        state = proposal["state"]

        if state == ProposalState.TRADING:
            raise ContractError.PROPOSAL_NOT_RESOLVED

        # Determine winning side
        if state == ProposalState.PASSED or state == ProposalState.EXECUTED:
            # YES wins
            bal = self.storage.get(("yes_balance", proposal_id, trader), U128(0))
            if bal < token_amount:
                raise ContractError.INSUFFICIENT_TOKENS
            self.storage.set(("yes_balance", proposal_id, trader), bal - token_amount)
        else:
            # NO wins
            bal = self.storage.get(("no_balance", proposal_id, trader), U128(0))
            if bal < token_amount:
                raise ContractError.INSUFFICIENT_TOKENS
            self.storage.set(("no_balance", proposal_id, trader), bal - token_amount)

        # Pay 1-to-1 collateral
        token = self.storage.get("collateral_token")
        self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), trader, token_amount])

        self.env.emit_event("winnings_redeemed", {
            "proposal_id": proposal_id,
            "trader": trader,
            "redeemed_amount": token_amount,
        })

    @view
    def get_market_prices(self, proposal_id: U64) -> Map:
        """Get current pool prices (scaled by 1e6)."""
        yes_col = self.storage.get(("yes_pool_collateral", proposal_id), U128(0))
        yes_tok = self.storage.get(("yes_pool_tokens", proposal_id), U128(0))

        no_col = self.storage.get(("no_pool_collateral", proposal_id), U128(0))
        no_tok = self.storage.get(("no_pool_tokens", proposal_id), U128(0))

        if yes_tok == U128(0) or no_tok == U128(0):
            return {"yes_price": U128(0), "no_price": U128(0)}

        yes_price = (yes_col * U128(1000000)) / yes_tok
        no_price = (no_col * U128(1000000)) / no_tok

        return {"yes_price": yes_price, "no_price": no_price}

    @view
    def get_trader_balances(self, proposal_id: U64, trader: Address) -> Map:
        """Get trader's YES and NO token balances."""
        return {
            "yes_balance": self.storage.get(("yes_balance", proposal_id, trader), U128(0)),
            "no_balance": self.storage.get(("no_balance", proposal_id, trader), U128(0)),
        }

    @view
    def get_proposal(self, proposal_id: U64) -> Map:
        """Get proposal details."""
        return self._get_proposal(proposal_id)

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _get_proposal(self, proposal_id: U64) -> Map:
        proposal = self.storage.get(("proposal", proposal_id), None)
        if proposal is None:
            raise ContractError.PROPOSAL_NOT_FOUND
        return proposal
