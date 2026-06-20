"""
Claims Arbitration — Decentralized dispute resolution and juror slashing.

Mycelium Smart Contract for Stellar
Provides decentralized arbitration for insurance claims. Supports juror capital
staking, pseudo-random juror selection, evidence locks, stake-weighted voting,
appeals panel triggers, and majority-incentive reward/slashing mechanics.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    INVALID_PARAMETERS = 4
    JUROR_NOT_STAKED = 5
    JUROR_ALREADY_STAKED = 6
    CASE_NOT_FOUND = 7
    CASE_RESOLVED = 8
    VOTING_PERIOD_ACTIVE = 9
    VOTING_PERIOD_CLOSED = 10
    NOT_SELECTED_JUROR = 11
    ALREADY_VOTED = 12
    INSUFFICIENT_STAKE = 13
    APPEAL_NOT_ALLOWED = 14
    APPEAL_PERIOD_EXPIRED = 15


class CaseStatus:
    OPEN = 1
    VOTING = 2
    RESOLVED = 3
    APPEALED = 4


@contract
class ClaimsArbitration:
    """
    Arbitration contract using staked juror pools, randomized panels,
    incentive-aligned slashing, and multi-tier appeals.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        asset_token: Address,
        min_juror_stake: U128,
        case_fee: U128,
        appeal_fee: U128,
        voting_duration_ledgers: U64,
        slash_ratio_bps: U64,  # e.g. 1000 bps = 10% slashing of minority juror stakes
    ):
        """Initialize the claims arbitration contract."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        if min_juror_stake == 0 or case_fee == 0 or appeal_fee == 0 or slash_ratio_bps > 10000:
            raise ContractError.INVALID_PARAMETERS

        self.storage.set("admin", admin)
        self.storage.set("asset_token", asset_token)
        self.storage.set("min_stake", min_juror_stake)
        self.storage.set("case_fee", case_fee)
        self.storage.set("appeal_fee", appeal_fee)
        self.storage.set("voting_duration", voting_duration_ledgers)
        self.storage.set("slash_ratio", slash_ratio_bps)
        self.storage.set("case_count", U64(0))
        self.storage.set("jurors_list", Vec())
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "asset_token": asset_token,
            "min_stake": min_juror_stake,
        })

    @external
    def stake_juror(self, juror: Address, amount: U128):
        """Stake asset tokens to join the juror pool."""
        juror.require_auth()
        self._require_initialized()

        min_stake = self.storage.get("min_stake")
        current_stake = self.storage.get(f"juror:{juror}:stake", U128(0))
        new_stake = current_stake + amount

        if new_stake < min_stake:
            raise ContractError.INVALID_PARAMETERS

        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, juror, self.env.current_contract(), amount)

        self.storage.set(f"juror:{juror}:stake", new_stake)

        jurors_list = self.storage.get("jurors_list")
        # Check if already in list
        in_list = False
        for i in range(len(jurors_list)):
            if jurors_list[i] == juror:
                in_list = True
                break

        if not in_list:
            jurors_list.append(juror)
            self.storage.set("jurors_list", jurors_list)

        self.env.emit_event("juror_staked", {
            "juror": juror,
            "amount": amount,
            "total_stake": new_stake,
        })

    @external
    def unstake_juror(self, juror: Address, amount: U128):
        """Withdraw juror stake. Juror must not be active in open cases."""
        juror.require_auth()
        self._require_initialized()

        current_stake = self.storage.get(f"juror:{juror}:stake", U128(0))
        if amount > current_stake:
            raise ContractError.INSUFFICIENT_STAKE

        new_stake = current_stake - amount
        min_stake = self.storage.get("min_stake")

        if new_stake > 0 and new_stake < min_stake:
            raise ContractError.INVALID_PARAMETERS

        # Check if juror is currently locked in any active cases (simplified via local flag)
        if self.storage.get(f"juror:{juror}:locked", False):
            raise ContractError.INVALID_PARAMETERS

        self.storage.set(f"juror:{juror}:stake", new_stake)

        if new_stake == 0:
            # Remove from list
            jurors_list = self.storage.get("jurors_list")
            new_list = Vec()
            for i in range(len(jurors_list)):
                if jurors_list[i] != juror:
                    new_list.append(jurors_list[i])
            self.storage.set("jurors_list", new_list)

        asset_token = self.storage.get("asset_token")
        self.env.transfer(asset_token, self.env.current_contract(), juror, amount)

        self.env.emit_event("juror_unstaked", {
            "juror": juror,
            "amount": amount,
            "remaining_stake": new_stake,
        })

    @external
    def file_dispute(
        self,
        claimant: Address,
        respondent: Address,
        evidence_hash: Bytes,
    ) -> U64:
        """Register a new insurance claim dispute, locking evidence and filing fee."""
        claimant.require_auth()
        self._require_initialized()

        case_fee = self.storage.get("case_fee")
        asset_token = self.storage.get("asset_token")

        # Collect case fee
        self.env.transfer(asset_token, claimant, self.env.current_contract(), case_fee)

        case_id = self.storage.get("case_count") + 1
        self.storage.set("case_count", case_id)

        self.storage.set(f"case:{case_id}:claimant", claimant)
        self.storage.set(f"case:{case_id}:respondent", respondent)
        self.storage.set(f"case:{case_id}:evidence", evidence_hash)
        self.storage.set(f"case:{case_id}:status", CaseStatus.OPEN)
        self.storage.set(f"case:{case_id}:fee_locked", case_fee)
        self.storage.set(f"case:{case_id}:jurors_count", U64(3))  # base jury size
        self.storage.set(f"case:{case_id}:appeals_count", U64(0))

        self.env.emit_event("dispute_filed", {
            "case_id": case_id,
            "claimant": claimant,
            "respondent": respondent,
            "evidence": evidence_hash,
        })

        return case_id

    @external
    def select_jurors(self, caller: Address, case_id: U64):
        """Select a jury panel pseudo-randomly from staked jurors."""
        caller.require_auth()
        self._require_initialized()

        status = self.storage.get(f"case:{case_id}:status", None)
        if status is None:
            raise ContractError.CASE_NOT_FOUND

        if status != CaseStatus.OPEN and status != CaseStatus.APPEALED:
            raise ContractError.INVALID_PARAMETERS

        jurors_list = self.storage.get("jurors_list")
        jurors_len = len(jurors_list)

        required_jurors = self.storage.get(f"case:{case_id}:jurors_count")
        if jurors_len < required_jurors:
            raise ContractError.INVALID_PARAMETERS  # Not enough staked jurors

        # Simple deterministic selection using hash of ledger index + case_id + index
        selected_jurors = Vec()
        ledger_seq = self.env.ledger().sequence()

        # Simple algorithm: index = (ledger_seq + case_id + idx) % jurors_len
        # In production, a secure randomness beacon would be preferred.
        # We loop until we get enough unique jurors
        attempts = U64(0)
        idx_offset = U64(0)

        while len(selected_jurors) < required_jurors and attempts < U64(100):
            attempts += 1
            index = (ledger_seq + case_id + idx_offset) % jurors_len
            candidate = jurors_list[index]
            idx_offset += 1

            # Check uniqueness
            already_selected = False
            for j in range(len(selected_jurors)):
                if selected_jurors[j] == candidate:
                    already_selected = True
                    break

            if not already_selected:
                selected_jurors.append(candidate)
                # Lock juror stake so they can't unstake during case resolution
                self.storage.set(f"juror:{candidate}:locked", True)

        if len(selected_jurors) < required_jurors:
            raise ContractError.INVALID_PARAMETERS

        # Store selected jurors
        self.storage.set(f"case:{case_id}:selected_jurors", selected_jurors)
        self.storage.set(f"case:{case_id}:status", CaseStatus.VOTING)

        voting_duration = self.storage.get("voting_duration")
        voting_end = ledger_seq + voting_duration
        self.storage.set(f"case:{case_id}:voting_end", voting_end)
        self.storage.set(f"case:{case_id}:yes_votes", U128(0))
        self.storage.set(f"case:{case_id}:no_votes", U128(0))

        self.env.emit_event("jury_selected", {
            "case_id": case_id,
            "voting_end": voting_end,
            "panel_size": required_jurors,
        })

    @external
    def cast_vote(self, juror: Address, case_id: U64, vote_yes: Bool):
        """Cast vote as a selected juror for the dispute."""
        juror.require_auth()
        self._require_initialized()

        status = self.storage.get(f"case:{case_id}:status", None)
        if status is None:
            raise ContractError.CASE_NOT_FOUND

        if status != CaseStatus.VOTING:
            raise ContractError.VOTING_PERIOD_CLOSED

        current_ledger = self.env.ledger().sequence()
        voting_end = self.storage.get(f"case:{case_id}:voting_end")
        if current_ledger > voting_end:
            raise ContractError.VOTING_PERIOD_CLOSED

        # Check if juror is selected
        selected_jurors = self.storage.get(f"case:{case_id}:selected_jurors")
        is_selected = False
        for i in range(len(selected_jurors)):
            if selected_jurors[i] == juror:
                is_selected = True
                break

        if not is_selected:
            raise ContractError.NOT_SELECTED_JUROR

        if self.storage.get(f"case:{case_id}:voted:{juror}", False):
            raise ContractError.ALREADY_VOTED

        juror_stake = self.storage.get(f"juror:{juror}:stake", U128(0))

        # Record vote
        self.storage.set(f"case:{case_id}:voted:{juror}", True)
        self.storage.set(f"case:{case_id}:vote_val:{juror}", vote_yes)

        if vote_yes:
            yes_votes = self.storage.get(f"case:{case_id}:yes_votes", U128(0))
            self.storage.set(f"case:{case_id}:yes_votes", yes_votes + juror_stake)
        else:
            no_votes = self.storage.get(f"case:{case_id}:no_votes", U128(0))
            self.storage.set(f"case:{case_id}:no_votes", no_votes + juror_stake)

        self.env.emit_event("arbitration_vote_cast", {
            "case_id": case_id,
            "juror": juror,
            "vote_yes": vote_yes,
            "weight": juror_stake,
        })

    @external
    def resolve_case(self, caller: Address, case_id: U64):
        """Finalize dispute, distribute fee rewards, and apply slashing to incoherent jurors."""
        caller.require_auth()
        self._require_initialized()

        status = self.storage.get(f"case:{case_id}:status", None)
        if status is None:
            raise ContractError.CASE_NOT_FOUND

        if status != CaseStatus.VOTING:
            raise ContractError.INVALID_PARAMETERS

        current_ledger = self.env.ledger().sequence()
        voting_end = self.storage.get(f"case:{case_id}:voting_end")
        if current_ledger <= voting_end:
            raise ContractError.VOTING_PERIOD_ACTIVE

        yes_votes = self.storage.get(f"case:{case_id}:yes_votes", U128(0))
        no_votes = self.storage.get(f"case:{case_id}:no_votes", U128(0))

        # In case of tie, default is No (claim invalid)
        ruling_yes = yes_votes > no_votes

        # Reward / Slash Logic
        selected_jurors = self.storage.get(f"case:{case_id}:selected_jurors")
        jurors_len = len(selected_jurors)

        coherent_jurors = Vec()
        incoherent_jurors = Vec()

        for idx in range(jurors_len):
            j = selected_jurors[idx]
            # Unlock juror
            self.storage.set(f"juror:{j}:locked", False)

            # Check if voted
            voted = self.storage.get(f"case:{case_id}:voted:{j}", False)
            if voted:
                vote_val = self.storage.get(f"case:{case_id}:vote_val:{j}")
                if vote_val == ruling_yes:
                    coherent_jurors.append(j)
                else:
                    incoherent_jurors.append(j)
            else:
                # Jurors who failed to vote are treated as incoherent and slashed
                incoherent_jurors.append(j)

        # Apply Slashing to incoherent jurors
        slash_ratio = self.storage.get("slash_ratio")
        total_slashed = U128(0)

        for i in range(len(incoherent_jurors)):
            inc_j = incoherent_jurors[i]
            stake = self.storage.get(f"juror:{inc_j}:stake", U128(0))
            slash_amount = (stake * U128(slash_ratio)) // U128(10000)
            if slash_amount > 0:
                self.storage.set(f"juror:{inc_j}:stake", stake - slash_amount)
                total_slashed += slash_amount

        # Distribute locked case fees + slashed stakes to coherent jurors
        case_fee = self.storage.get(f"case:{case_id}:fee_locked")
        total_rewards = case_fee + total_slashed

        coherent_count = len(coherent_jurors)
        asset_token = self.storage.get("asset_token")

        if coherent_count > 0:
            reward_per_juror = total_rewards // U128(coherent_count)
            for i in range(coherent_count):
                coh_j = coherent_jurors[i]
                # Payout reward directly
                self.env.transfer(asset_token, self.env.current_contract(), coh_j, reward_per_juror)
        else:
            # If no coherent jurors (or all tied/didn't vote), return fee to claimant
            claimant = self.storage.get(f"case:{case_id}:claimant")
            self.env.transfer(asset_token, self.env.current_contract(), claimant, case_fee)

        self.storage.set(f"case:{case_id}:status", CaseStatus.RESOLVED)
        self.storage.set(f"case:{case_id}:ruling", ruling_yes)

        self.env.emit_event("case_resolved", {
            "case_id": case_id,
            "ruling_yes": ruling_yes,
            "coherent_jurors": coherent_count,
            "total_slashed": total_slashed,
        })

    @external
    def appeal_ruling(self, caller: Address, case_id: U64):
        """Appeal the current ruling of a case. Requires appeal fee and increases jury panel size."""
        caller.require_auth()
        self._require_initialized()

        status = self.storage.get(f"case:{case_id}:status", None)
        if status is None:
            raise ContractError.CASE_NOT_FOUND

        if status != CaseStatus.RESOLVED:
            raise ContractError.APPEAL_NOT_ALLOWED

        appeals_count = self.storage.get(f"case:{case_id}:appeals_count", U64(0))
        # Limit to maximum 2 appeals
        if appeals_count >= U64(2):
            raise ContractError.APPEAL_NOT_ALLOWED

        appeal_fee = self.storage.get("appeal_fee")
        asset_token = self.storage.get("asset_token")

        # Collect appeal fee from appellant
        self.env.transfer(asset_token, caller, self.env.current_contract(), appeal_fee)

        # Scale jury size: appeal 1 -> 7 jurors, appeal 2 -> 13 jurors
        current_jurors = self.storage.get(f"case:{case_id}:jurors_count")
        new_jurors_count = current_jurors * U64(2) + U64(1)

        self.storage.set(f"case:{case_id}:status", CaseStatus.APPEALED)
        self.storage.set(f"case:{case_id}:jurors_count", new_jurors_count)
        self.storage.set(f"case:{case_id}:appeals_count", appeals_count + 1)
        self.storage.set(f"case:{case_id}:fee_locked", appeal_fee)

        # Clean old votes
        selected_jurors = self.storage.get(f"case:{case_id}:selected_jurors")
        for i in range(len(selected_jurors)):
            self.storage.set(f"case:{case_id}:voted:{selected_jurors[i]}", False)

        self.env.emit_event("case_appealed", {
            "case_id": case_id,
            "new_jury_size": new_jurors_count,
            "appeal_number": appeals_count + 1,
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_case(self, case_id: U64) -> Map:
        """Get information and current state of a dispute case."""
        status = self.storage.get(f"case:{case_id}:status", None)
        if status is None:
            raise ContractError.CASE_NOT_FOUND

        ruling = False
        if status == CaseStatus.RESOLVED:
            ruling = self.storage.get(f"case:{case_id}:ruling")

        return {
            "case_id": case_id,
            "claimant": self.storage.get(f"case:{case_id}:claimant"),
            "respondent": self.storage.get(f"case:{case_id}:respondent"),
            "status": status,
            "evidence": self.storage.get(f"case:{case_id}:evidence"),
            "jurors_count": self.storage.get(f"case:{case_id}:jurors_count"),
            "appeals_count": self.storage.get(f"case:{case_id}:appeals_count"),
            "ruling": ruling,
        }

    @view
    def get_juror_stake(self, juror: Address) -> U128:
        """Get current stake amount of a juror."""
        return self.storage.get(f"juror:{juror}:stake", U128(0))

    @view
    def get_staked_jurors(self) -> Vec:
        """Retrieve complete list of registered jurors."""
        return self.storage.get("jurors_list")

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED
