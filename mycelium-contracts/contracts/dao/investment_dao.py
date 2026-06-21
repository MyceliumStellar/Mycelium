"""
Investment DAO — Capital contributions, NAV-based shares, investment proposals, HWM manager carry fees, and member exits.

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
    INVALID_AMOUNT = 4
    PROPOSAL_NOT_FOUND = 5
    INVALID_STATE = 6
    ALREADY_VOTED = 7
    INSUFFICIENT_FUNDS = 8
    INSUFFICIENT_SHARES = 9
    VOTING_ACTIVE = 10
    VOTING_ENDED = 11
    ZERO_SHARES = 12


class ProposalState:
    ACTIVE = 0
    DEFEATED = 1
    SUCCEEDED = 2
    EXECUTED = 3


@contract
class InvestmentDAO:
    """An investment club contract tracking capital, voting, NAV, performance carry, and exits."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        manager: Address,
        deposit_token: Address,
        carry_fee_bps: U64,
        voting_duration: U64,
        quorum_bps: U64,
    ):
        """Initialize the Investment Club contract.

        Args:
            manager: Club manager address who proposes investments and updates valuations.
            deposit_token: Asset token used for deposits (e.g. USDC).
            carry_fee_bps: Performance fee in basis points (e.g. 2000 = 20%).
            voting_duration: Length of voting period for investment proposals.
            quorum_bps: Quorum in basis points.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        manager.require_auth()

        if carry_fee_bps > U64(5000):  # Cap carry fee at 50%
            raise ContractError.INVALID_AMOUNT
        if quorum_bps > U64(10000) or quorum_bps == U64(0):
            raise ContractError.INVALID_AMOUNT

        self.storage.set("manager", manager)
        self.storage.set("deposit_token", deposit_token)
        self.storage.set("carry_fee_bps", carry_fee_bps)
        self.storage.set("voting_duration", voting_duration)
        self.storage.set("quorum_bps", quorum_bps)

        self.storage.set("total_shares", U128(0))
        self.storage.set("proposal_count", U64(0))
        self.storage.set("high_water_mark", U128(1000000)) # Start HWM at 1.00 (scaled by 1e6)
        self.storage.set("total_assets_valuation", U128(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "manager": manager,
            "deposit_token": deposit_token,
            "carry_fee_bps": carry_fee_bps,
        })

    @external
    def deposit(self, member: Address, amount: U128) -> U128:
        """Contribute capital to the investment club in exchange for shares.

        Args:
            member: Address depositing capital.
            amount: Amount of deposit tokens.

        Returns:
            The number of club shares minted to the depositor.
        """
        self._require_initialized()
        member.require_auth()

        if amount == U128(0):
            raise ContractError.INVALID_AMOUNT

        token = self.storage.get("deposit_token")
        # Pre-deposit cash balance check (excluding the incoming amount)
        cash_balance_before = self.env.invoke_contract(token, "balance", [self.env.current_contract_address()])

        # Transfer tokens to contract
        transfer_success = self.env.invoke_contract(token, "transfer", [member, self.env.current_contract_address(), amount])
        if not transfer_success:
            raise ContractError.INSUFFICIENT_FUNDS

        total_shares = self.storage.get("total_shares")
        shares_to_mint = U128(0)

        if total_shares == U128(0):
            # If first contribution, mint shares 1-to-1 with amount
            shares_to_mint = amount
        else:
            # NAV = cash before + active asset valuations
            assets_val = self.storage.get("total_assets_valuation")
            nav = cash_balance_before + assets_val
            # shares = amount * total_shares / nav
            shares_to_mint = (amount * total_shares) / nav

        if shares_to_mint == U128(0):
            raise ContractError.ZERO_SHARES

        # Update member shares and total shares
        member_shares = self.storage.get(("shares", member), U128(0))
        self.storage.set(("shares", member), member_shares + shares_to_mint)
        self.storage.set("total_shares", total_shares + shares_to_mint)

        # Update member contributions
        contrib = self.storage.get(("contribution", member), U128(0))
        self.storage.set(("contribution", member), contrib + amount)

        self.env.emit_event("deposit", {
            "member": member,
            "amount": amount,
            "shares_minted": shares_to_mint,
        })

        return shares_to_mint

    @external
    def propose_investment(
        self,
        caller: Address,
        target: Address,
        amount: U128,
        description: Symbol,
    ) -> U64:
        """Propose an investment. Only manager.

        Args:
            caller: Must be manager.
            target: The investment contract/address.
            amount: The capital to deploy.
            description: Description symbol.
        """
        self._require_initialized()
        caller.require_auth()

        manager = self.storage.get("manager")
        if caller != manager:
            raise ContractError.UNAUTHORIZED

        token = self.storage.get("deposit_token")
        cash_balance = self.env.invoke_contract(token, "balance", [self.env.current_contract_address()])
        if cash_balance < amount:
            raise ContractError.INSUFFICIENT_FUNDS

        proposal_id = self.storage.get("proposal_count") + U64(1)
        self.storage.set("proposal_count", proposal_id)

        now = self.env.ledger().timestamp()
        duration = self.storage.get("voting_duration")
        vote_end = now + duration

        proposal = {
            "id": proposal_id,
            "target": target,
            "amount": amount,
            "description": description,
            "vote_end": vote_end,
            "votes_for": U128(0),
            "votes_against": U128(0),
            "executed": False,
        }

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("investment_proposed", {
            "proposal_id": proposal_id,
            "target": target,
            "amount": amount,
            "vote_end": vote_end,
        })

        return proposal_id

    @external
    def cast_vote(self, voter: Address, proposal_id: U64, vote_type: U64):
        """Cast vote using member shares as weight.

        Args:
            voter: Club member address.
            proposal_id: Proposal ID.
            vote_type: 0 for AGAINST, 1 for FOR.
        """
        self._require_initialized()
        voter.require_auth()

        voter_shares = self.storage.get(("shares", voter), U128(0))
        if voter_shares == U128(0):
            raise ContractError.UNAUTHORIZED

        proposal = self._get_proposal(proposal_id)
        state = self._compute_state(proposal)
        if state != ProposalState.ACTIVE:
            raise ContractError.INVALID_STATE

        already_voted = self.storage.get(("voted", proposal_id, voter), False)
        if already_voted:
            raise ContractError.ALREADY_VOTED

        if vote_type == U64(1):
            proposal["votes_for"] = proposal["votes_for"] + voter_shares
        elif vote_type == U64(0):
            proposal["votes_against"] = proposal["votes_against"] + voter_shares
        else:
            raise ContractError.INVALID_STATE

        self.storage.set(("proposal", proposal_id), proposal)
        self.storage.set(("voted", proposal_id, voter), True)

        self.env.emit_event("vote_cast", {
            "proposal_id": proposal_id,
            "voter": voter,
            "vote_type": vote_type,
            "weight": voter_shares,
        })

    @external
    def execute_investment(self, caller: Address, proposal_id: U64):
        """Deploy capital to the investment if voting succeeds. Only manager.

        Args:
            caller: Must be manager.
            proposal_id: Proposal ID.
        """
        self._require_initialized()
        caller.require_auth()

        manager = self.storage.get("manager")
        if caller != manager:
            raise ContractError.UNAUTHORIZED

        proposal = self._get_proposal(proposal_id)
        state = self._compute_state(proposal)
        if state != ProposalState.SUCCEEDED:
            raise ContractError.INVALID_STATE

        token = self.storage.get("deposit_token")
        cash_balance = self.env.invoke_contract(token, "balance", [self.env.current_contract_address()])
        if cash_balance < proposal["amount"]:
            raise ContractError.INSUFFICIENT_FUNDS

        # Deploy cash
        transfer_success = self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), proposal["target"], proposal["amount"]])
        if not transfer_success:
            raise ContractError.INSUFFICIENT_FUNDS

        # Register valuation of this new investment asset
        asset_address = proposal["target"]
        self.storage.set(("asset_valuation", asset_address), proposal["amount"])

        # Update total active asset valuations
        assets_val = self.storage.get("total_assets_valuation")
        self.storage.set("total_assets_valuation", assets_val + proposal["amount"])

        proposal["executed"] = True
        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("investment_executed", {
            "proposal_id": proposal_id,
            "target": asset_address,
            "amount": proposal["amount"],
        })

    @external
    def update_asset_valuation(self, caller: Address, asset: Address, new_value: U128):
        """Update valuation of an active investment asset and assess manager carry fee. Only manager.

        Args:
            caller: Must be manager.
            asset: Asset address being valued.
            new_value: New valuation in deposit token terms.
        """
        self._require_initialized()
        caller.require_auth()

        manager = self.storage.get("manager")
        if caller != manager:
            raise ContractError.UNAUTHORIZED

        old_value = self.storage.get(("asset_valuation", asset), U128(0))
        self.storage.set(("asset_valuation", asset), new_value)

        # Update overall assets valuation
        assets_val = self.storage.get("total_assets_valuation")
        updated_assets_val = (assets_val - old_value) + new_value
        self.storage.set("total_assets_valuation", updated_assets_val)

        self.env.emit_event("valuation_updated", {
            "asset": asset,
            "old_value": old_value,
            "new_value": new_value,
        })

        # Carry fee logic using High-Water Mark (HWM)
        total_shares = self.storage.get("total_shares")
        if total_shares > U128(0):
            token = self.storage.get("deposit_token")
            cash = self.env.invoke_contract(token, "balance", [self.env.current_contract_address()])
            nav = cash + updated_assets_val

            # share_price (scaled by 1e6) = nav * 1e6 / total_shares
            share_price = (nav * U128(1000000)) / total_shares
            hwm = self.storage.get("high_water_mark")

            if share_price > hwm:
                profit_per_share = share_price - hwm
                total_profit = (profit_per_share * total_shares) / U128(1000000)

                carry_fee_bps = self.storage.get("carry_fee_bps")
                carry_profit = (total_profit * U128(carry_fee_bps)) / U128(10000)

                # Convert performance carry profit to shares to mint to manager
                # manager_shares = carry_profit * total_shares / (nav - carry_profit)
                if nav > carry_profit:
                    carry_shares = (carry_profit * total_shares) / (nav - carry_profit)
                    if carry_shares > U128(0):
                        manager_shares = self.storage.get(("shares", manager), U128(0))
                        self.storage.set(("shares", manager), manager_shares + carry_shares)
                        self.storage.set("total_shares", total_shares + carry_shares)

                        # New NAV per share will adjust, recalculate HWM to the post-fee share price
                        new_nav = nav # nav doesn't change, but total shares does
                        new_share_price = (new_nav * U128(1000000)) / (total_shares + carry_shares)
                        self.storage.set("high_water_mark", new_share_price)

                        self.env.emit_event("carry_fee_minted", {
                            "manager": manager,
                            "shares_minted": carry_shares,
                            "new_hwm": new_share_price,
                        })

    @external
    def exit(self, member: Address, shares_to_burn: U128):
        """Redeem shares for a proportional share of available liquid cash.

        Args:
            member: Club member address.
            shares_to_burn: Shares they wish to redeem.
        """
        self._require_initialized()
        member.require_auth()

        member_shares = self.storage.get(("shares", member), U128(0))
        if member_shares < shares_to_burn or shares_to_burn == U128(0):
            raise ContractError.INSUFFICIENT_SHARES

        total_shares = self.storage.get("total_shares")
        token = self.storage.get("deposit_token")
        cash = self.env.invoke_contract(token, "balance", [self.env.current_contract_address()])
        assets_val = self.storage.get("total_assets_valuation")
        nav = cash + assets_val

        # Proportional payout: payout = shares_to_burn * NAV / total_shares
        payout = (shares_to_burn * nav) / total_shares

        # Exit requires the fund to have enough liquid cash to pay the exit
        if cash < payout:
            raise ContractError.INSUFFICIENT_FUNDS

        # Update user and globals
        self.storage.set(("shares", member), member_shares - shares_to_burn)
        self.storage.set("total_shares", total_shares - shares_to_burn)

        # Transfer tokens
        success = self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), member, payout])
        if not success:
            raise ContractError.INSUFFICIENT_FUNDS

        self.env.emit_event("exit", {
            "member": member,
            "shares_burned": shares_to_burn,
            "payout": payout,
        })

    @view
    def get_nav(self) -> U128:
        """Calculate and return the current Net Asset Value (NAV)."""
        token = self.storage.get("deposit_token")
        cash = self.env.invoke_contract(token, "balance", [self.env.current_contract_address()])
        assets_val = self.storage.get("total_assets_valuation")
        return cash + assets_val

    @view
    def get_share_price(self) -> U128:
        """Get NAV per share scaled by 1e6."""
        total_shares = self.storage.get("total_shares")
        if total_shares == U128(0):
            return U128(1000000) # Base 1.00
        return (self.get_nav() * U128(1000000)) / total_shares

    @view
    def get_member_info(self, member: Address) -> Map:
        """Get shares and contributions of a member."""
        return {
            "shares": self.storage.get(("shares", member), U128(0)),
            "contribution": self.storage.get(("contribution", member), U128(0)),
        }

    @view
    def get_proposal(self, proposal_id: U64) -> Map:
        """Get details and state of a proposal."""
        proposal = self._get_proposal(proposal_id)
        proposal["state"] = self._compute_state(proposal)
        return proposal

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

    def _compute_state(self, proposal: Map) -> U64:
        if proposal["executed"]:
            return ProposalState.EXECUTED

        now = self.env.ledger().timestamp()
        if now < proposal["vote_end"]:
            return ProposalState.ACTIVE

        total_shares = self.storage.get("total_shares")
        quorum_bps = self.storage.get("quorum_bps")
        required_quorum = (total_shares * U128(quorum_bps)) / U128(10000)

        total_votes = proposal["votes_for"] + proposal["votes_against"]
        if total_votes < required_quorum:
            return ProposalState.DEFEATED

        if proposal["votes_for"] > proposal["votes_against"]:
            return ProposalState.SUCCEEDED
        else:
            return ProposalState.DEFEATED
