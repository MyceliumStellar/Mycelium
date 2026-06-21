"""
Conviction Voting — Continuous conviction accumulation, dynamic passing thresholds, decay curves, and token staking.

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
    INSUFFICIENT_STAKE = 5
    INVALID_AMOUNT = 6
    PROPOSAL_ALREADY_PASSED = 7
    PROPOSAL_DEFEATED = 8
    THRESHOLD_NOT_MET = 9
    OVERSPENDING = 10


@contract
class ConvictionVoting:
    """Continuous conviction voting governance with alpha-decay accumulation and dynamic thresholds."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        token: Address,
        alpha: U64,
        max_ratio_bps: U64,
    ):
        """Initialize the Conviction Voting contract.

        Args:
            admin: Admin address.
            token: Voting token address.
            alpha: Decay factor in basis points (e.g., 9000 = 0.90 per time step).
            max_ratio_bps: Max amount of treasury that can be requested in a single proposal (bps).
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        if alpha >= 10000 or alpha == 0:
            raise ContractError.INVALID_AMOUNT
        if max_ratio_bps >= 10000 or max_ratio_bps == 0:
            raise ContractError.INVALID_AMOUNT

        self.storage.set("admin", admin)
        self.storage.set("token", token)
        self.storage.set("alpha", alpha)
        self.storage.set("max_ratio_bps", max_ratio_bps)
        self.storage.set("proposal_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "token": token,
            "alpha": alpha,
        })

    @external
    def propose_funding(
        self,
        proposer: Address,
        recipient: Address,
        requested_amount: U128,
        description: Symbol,
    ) -> U64:
        """Propose a funding payout. Threshold will depend on request size.

        Args:
            proposer: Proposing address.
            recipient: Payment recipient.
            requested_amount: Payout size.
            description: Payout description.
        """
        self._require_initialized()
        proposer.require_auth()

        if requested_amount == U128(0):
            raise ContractError.INVALID_AMOUNT

        # Verify proposal amount doesn't exceed maximum ratio of treasury
        token = self.storage.get("token")
        treasury_balance = self.env.invoke_contract(token, "balance", [self.env.current_contract_address()])
        max_ratio_bps = self.storage.get("max_ratio_bps")
        max_allowed = (treasury_balance * U128(max_ratio_bps)) / U128(10000)

        if requested_amount > max_allowed:
            raise ContractError.OVERSPENDING

        proposal_id = self.storage.get("proposal_count") + U64(1)
        self.storage.set("proposal_count", proposal_id)

        now = self.env.ledger().timestamp()

        proposal = {
            "id": proposal_id,
            "proposer": proposer,
            "recipient": recipient,
            "requested_amount": requested_amount,
            "description": description,
            "accumulated_conviction": U128(0),
            "total_staked": U128(0),
            "last_update_time": now,
            "passed": False,
        }

        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("proposal_created", {
            "proposal_id": proposal_id,
            "proposer": proposer,
            "amount": requested_amount,
        })

        return proposal_id

    @external
    def stake_to_proposal(self, voter: Address, proposal_id: U64, amount: U128):
        """Stake voting tokens to back a specific proposal, generating conviction.

        Args:
            voter: Voter address.
            proposal_id: Target proposal.
            amount: Stake size to add.
        """
        self._require_initialized()
        voter.require_auth()

        # Update proposal conviction first before adding stake
        self._update_conviction(proposal_id)

        # Deposit/Escrow tokens from voter
        token = self.storage.get("token")
        success = self.env.invoke_contract(token, "transfer", [voter, self.env.current_contract_address(), amount])
        if not success:
            raise ContractError.INSUFFICIENT_STAKE

        proposal = self._get_proposal(proposal_id)
        if proposal["passed"]:
            raise ContractError.PROPOSAL_ALREADY_PASSED

        # Update voter stake registry
        current_voter_stake = self.storage.get(("stake", proposal_id, voter), U128(0))
        self.storage.set(("stake", proposal_id, voter), current_voter_stake + amount)

        # Update proposal aggregate stake
        proposal["total_staked"] = proposal["total_staked"] + amount
        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("stake_added", {
            "proposal_id": proposal_id,
            "voter": voter,
            "amount": amount,
            "total_staked": proposal["total_staked"],
        })

    @external
    def withdraw_stake(self, voter: Address, proposal_id: U64, amount: U128):
        """Withdraw/unstake tokens, triggering conviction decay.

        Args:
            voter: Voter address.
            proposal_id: Target proposal.
            amount: Stake size to withdraw.
        """
        self._require_initialized()
        voter.require_auth()

        # Update proposal conviction before reducing stake
        self._update_conviction(proposal_id)

        voter_stake = self.storage.get(("stake", proposal_id, voter), U128(0))
        if voter_stake < amount:
            raise ContractError.INSUFFICIENT_STAKE

        proposal = self._get_proposal(proposal_id)

        # Update voter stake registry
        self.storage.set(("stake", proposal_id, voter), voter_stake - amount)

        # Update proposal aggregate stake
        proposal["total_staked"] = proposal["total_staked"] - amount
        self.storage.set(("proposal", proposal_id), proposal)

        # Return tokens to voter
        token = self.storage.get("token")
        self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), voter, amount])

        self.env.emit_event("stake_withdrawn", {
            "proposal_id": proposal_id,
            "voter": voter,
            "amount": amount,
            "total_staked": proposal["total_staked"],
        })

    @external
    def execute_funding(self, caller: Address, proposal_id: U64):
        """Execute proposal if conviction matches or exceeds the dynamic threshold.

        Args:
            caller: Trigger address.
            proposal_id: Proposal ID.
        """
        self._require_initialized()
        caller.require_auth()

        # Refresh conviction to latest timestamp
        self._update_conviction(proposal_id)

        proposal = self._get_proposal(proposal_id)
        if proposal["passed"]:
            raise ContractError.PROPOSAL_ALREADY_PASSED

        # Calculate threshold
        threshold = self._get_required_threshold(proposal_id)
        if proposal["accumulated_conviction"] < threshold:
            raise ContractError.THRESHOLD_NOT_MET

        proposal["passed"] = True
        self.storage.set(("proposal", proposal_id), proposal)

        # Execute transfer
        token = self.storage.get("token")
        success = self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), proposal["recipient"], proposal["requested_amount"]])
        if not success:
            proposal["passed"] = False
            self.storage.set(("proposal", proposal_id), proposal)
            raise ContractError.OVERSPENDING

        self.env.emit_event("proposal_executed", {
            "proposal_id": proposal_id,
            "recipient": proposal["recipient"],
            "amount": proposal["requested_amount"],
        })

    @view
    def get_conviction(self, proposal_id: U64) -> Map:
        """Read accumulated conviction and target threshold without state modification."""
        proposal = self._get_proposal(proposal_id)

        # Calculate conviction up to current time (read-only calculation)
        now = self.env.ledger().timestamp()
        dt = now - proposal["last_update_time"]

        acc = proposal["accumulated_conviction"]
        weight = proposal["total_staked"]

        alpha = self.storage.get("alpha")
        decay = self._power_bps(alpha, dt)

        # Conviction = accumulated * decay + weight * (1 - decay)
        current_conviction = (acc * decay) / U128(10000) + (weight * (U128(10000) - decay)) / U128(10000)
        threshold = self._get_required_threshold(proposal_id)

        return {
            "current_conviction": current_conviction,
            "required_threshold": threshold,
            "total_staked": weight,
            "passed": proposal["passed"],
        }

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

    def _update_conviction(self, proposal_id: U64):
        proposal = self._get_proposal(proposal_id)
        now = self.env.ledger().timestamp()
        dt = now - proposal["last_update_time"]

        if dt > U64(0):
            acc = proposal["accumulated_conviction"]
            weight = proposal["total_staked"]

            alpha = self.storage.get("alpha")
            decay = self._power_bps(alpha, dt)

            # Conviction = accumulated * decay + weight * (1 - decay)
            proposal["accumulated_conviction"] = (acc * decay) / U128(10000) + (weight * (U128(10000) - decay)) / U128(10000)
            proposal["last_update_time"] = now
            self.storage.set(("proposal", proposal_id), proposal)

    def _get_required_threshold(self, proposal_id: U64) -> U128:
        proposal = self._get_proposal(proposal_id)
        token = self.storage.get("token")
        treasury_balance = self.env.invoke_contract(token, "balance", [self.env.current_contract_address()])

        if treasury_balance == U128(0):
            return U128(99999999999999999999) # Return extremely high threshold

        # Threshold formula: threshold = beta * total_supply / (1 - requested/treasury)^2
        # Let's simplify: threshold = total_supply / (1 - requested/treasury)^2
        # = total_supply * treasury^2 / (treasury - requested)^2
        # To maintain scaling and prevent overflow, compute:
        total_supply = self.env.invoke_contract(token, "total_supply", [])

        req = proposal["requested_amount"]
        if req >= treasury_balance:
            return U128(99999999999999999999)

        diff = treasury_balance - req
        # threshold = total_supply * treasury^2 / diff^2
        # Let's scale:
        numerator = total_supply * treasury_balance * treasury_balance
        denominator = diff * diff
        return numerator / denominator

    def _power_bps(self, base_bps: U64, exponent: U64) -> U128:
        """Fast power calculation for basis points: (base_bps / 10000) ^ exponent."""
        if exponent == U64(0):
            return U128(10000) # 1.0 in bps

        res = U128(10000)
        temp_base = U128(base_bps)
        exp = exponent

        while exp > U64(0):
            if exp % U64(2) == U64(1):
                res = (res * temp_base) / U128(10000)
            temp_base = (temp_base * temp_base) / U128(10000)
            exp = exp / U64(2)

        return res
