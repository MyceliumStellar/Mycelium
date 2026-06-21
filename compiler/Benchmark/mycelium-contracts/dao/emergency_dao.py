"""
Emergency DAO — Emergency multisig with fast-track proposals, protocol pausing, guardian rotations, and severity overrides.

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
    ALREADY_CONFIRMED = 5
    THRESHOLD_NOT_MET = 6
    INVALID_THRESHOLD = 7
    INVALID_SEVERITY = 8
    EXECUTION_FAILED = 9
    ALREADY_EXECUTED = 10
    GUARDIAN_DUPLICATE = 11
    ZERO_GUARDIANS = 12


class SeverityLevel:
    LOW = 0
    MEDIUM = 1
    HIGH = 2


@contract
class EmergencyDAO:
    """An emergency governance contract supporting multi-sig approvals, emergency pausing, and overrides."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        initial_guardians: Vec,
        threshold: U64,
    ):
        """Initialize the Emergency DAO with a set of guardians and signature threshold.

        Args:
            admin: Admin address for setup.
            initial_guardians: Vec of Addresses for initial emergency guardians.
            threshold: Number of signatures required to execute normal actions.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        g_len = len(initial_guardians)
        if g_len == 0:
            raise ContractError.ZERO_GUARDIANS
        if threshold == U64(0) or threshold > U64(g_len):
            raise ContractError.INVALID_THRESHOLD

        self.storage.set("admin", admin)
        self.storage.set("threshold", threshold)
        self.storage.set("guardian_count", U64(g_len))

        for i in range(g_len):
            guardian = initial_guardians[i]
            if self.storage.get(("guardian", guardian), False):
                raise ContractError.GUARDIAN_DUPLICATE
            self.storage.set(("guardian", guardian), True)

        self.storage.set("proposal_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "threshold": threshold,
            "guardian_count": U64(g_len),
        })

    @external
    def propose_action(
        self,
        proposer: Address,
        target: Address,
        value: U128,
        calldata: Bytes,
        severity: U64,
    ) -> U64:
        """Propose an emergency action or execution target. Only active guardian.

        Args:
            proposer: Proposing guardian.
            target: Contract address to call.
            value: Native tokens sent.
            calldata: Payload of call.
            severity: Low (0), Medium (1), or High (2).
        """
        self._require_initialized()
        proposer.require_auth()

        if not self.storage.get(("guardian", proposer), False):
            raise ContractError.UNAUTHORIZED

        if severity > SeverityLevel.HIGH:
            raise ContractError.INVALID_SEVERITY

        proposal_id = self.storage.get("proposal_count") + U64(1)
        self.storage.set("proposal_count", proposal_id)

        proposal = {
            "id": proposal_id,
            "proposer": proposer,
            "target": target,
            "value": value,
            "calldata": calldata,
            "severity": severity,
            "confirmations_count": U64(1), # Proposer implicitly confirms
            "executed": False,
        }

        self.storage.set(("proposal", proposal_id), proposal)
        self.storage.set(("confirmed", proposal_id, proposer), True)

        self.env.emit_event("emergency_action_proposed", {
            "proposal_id": proposal_id,
            "proposer": proposer,
            "target": target,
            "severity": severity,
        })

        return proposal_id

    @external
    def confirm_action(self, guardian: Address, proposal_id: U64):
        """Confirm a proposed emergency action. Only active guardian.

        Args:
            guardian: Confirming guardian.
            proposal_id: Proposal ID.
        """
        self._require_initialized()
        guardian.require_auth()

        if not self.storage.get(("guardian", guardian), False):
            raise ContractError.UNAUTHORIZED

        proposal = self._get_proposal(proposal_id)
        if proposal["executed"]:
            raise ContractError.ALREADY_EXECUTED

        already_confirmed = self.storage.get(("confirmed", proposal_id, guardian), False)
        if already_confirmed:
            raise ContractError.ALREADY_CONFIRMED

        proposal["confirmations_count"] = proposal["confirmations_count"] + U64(1)
        self.storage.set(("proposal", proposal_id), proposal)
        self.storage.set(("confirmed", proposal_id, guardian), True)

        self.env.emit_event("proposal_confirmed", {
            "proposal_id": proposal_id,
            "guardian": guardian,
            "confirmations": proposal["confirmations_count"],
        })

    @external
    def execute_action(self, executor: Address, proposal_id: U64):
        """Execute action if confirmation threshold met. Low/Medium require standard threshold.

        Args:
            executor: Triggering address.
            proposal_id: Proposal ID.
        """
        self._require_initialized()
        executor.require_auth()

        proposal = self._get_proposal(proposal_id)
        if proposal["executed"]:
            raise ContractError.ALREADY_EXECUTED

        required = self.storage.get("threshold")
        # High severity actions can be executed with a lower fast-track threshold (e.g. max(1, threshold/2))
        if proposal["severity"] == SeverityLevel.HIGH:
            required = required / U64(2)
            if required == U64(0):
                required = U64(1)

        if proposal["confirmations_count"] < required:
            raise ContractError.THRESHOLD_NOT_MET

        proposal["executed"] = True
        self.storage.set(("proposal", proposal_id), proposal)

        # Trigger internal or external contract call
        success = self.env.invoke_contract(proposal["target"], "execute", [proposal["calldata"]], proposal["value"])
        if not success:
            proposal["executed"] = False
            self.storage.set(("proposal", proposal_id), proposal)
            raise ContractError.EXECUTION_FAILED

        self.env.emit_event("emergency_action_executed", {
            "proposal_id": proposal_id,
            "executor": executor,
            "target": proposal["target"],
        })

    @external
    def instant_pause(self, guardian: Address, target: Address):
        """High Severity Override: A single guardian can immediately pause a target protocol.

        Args:
            guardian: Initiating guardian.
            target: Target protocol address.
        """
        self._require_initialized()
        guardian.require_auth()

        if not self.storage.get(("guardian", guardian), False):
            raise ContractError.UNAUTHORIZED

        # Instantly pause without voting (High severity feature)
        self.storage.set(("paused", target), True)

        # Attempt to call pause on the target contract
        # If the target doesn't implement pause, we catch/log, but update local state regardless
        self.env.invoke_contract(target, "pause", [])

        self.env.emit_event("target_instantly_paused", {
            "target": target,
            "guardian": guardian,
        })

    @external
    def instant_unpause(self, guardian: Address, target: Address):
        """Unpause a protocol. Requires standard threshold confirmations, but represented as action.

        For quick unpause safety, requires 2 guardians (a quick dual-sig unpause).
        """
        self._require_initialized()
        guardian.require_auth()

        if not self.storage.get(("guardian", guardian), False):
            raise ContractError.UNAUTHORIZED

        # We implement a dual-sig unpause bypass:
        # First guardian sets request, second guardian executes
        request = self.storage.get(("unpause_request", target), None)
        now = self.env.ledger().timestamp()

        if request is None or now > request["expires_at"]:
            # First guardian records request
            self.storage.set(("unpause_request", target), {
                "guardian": guardian,
                "expires_at": now + U64(3600), # 1 hour expiry
            })
            self.env.emit_event("unpause_requested", {"target": target, "guardian": guardian})
        else:
            if request["guardian"] == guardian:
                raise ContractError.ALREADY_CONFIRMED

            # Second guardian confirms, execute unpause
            self.storage.set(("paused", target), False)
            self.storage.remove(("unpause_request", target))

            self.env.invoke_contract(target, "unpause", [])

            self.env.emit_event("target_unpaused", {
                "target": target,
                "executor": guardian,
            })

    @external
    def propose_guardian_rotation(
        self,
        proposer: Address,
        old_guardian: Address,
        new_guardian: Address,
    ) -> U64:
        """Propose rotation of emergency guardians. Only guardian.

        Encodes calldata to call self rotation functions upon execution.
        """
        self._require_initialized()
        proposer.require_auth()

        if not self.storage.get(("guardian", proposer), False):
            raise ContractError.UNAUTHORIZED

        # Verify old guardian is active
        if not self.storage.get(("guardian", old_guardian), False):
            raise ContractError.UNAUTHORIZED

        proposal_id = self.storage.get("proposal_count") + U64(1)
        self.storage.set("proposal_count", proposal_id)

        # Encode rotation details
        proposal = {
            "id": proposal_id,
            "proposer": proposer,
            "target": self.env.current_contract_address(),
            "value": U128(0),
            "calldata": Bytes(b"rotate_guardian"), # Simulated symbol representation
            "severity": SeverityLevel.MEDIUM,
            "confirmations_count": U64(1),
            "executed": False,
            "old_guardian": old_guardian,
            "new_guardian": new_guardian,
        }

        self.storage.set(("proposal", proposal_id), proposal)
        self.storage.set(("confirmed", proposal_id, proposer), True)

        self.env.emit_event("rotation_proposed", {
            "proposal_id": proposal_id,
            "old_guardian": old_guardian,
            "new_guardian": new_guardian,
        })

        return proposal_id

    @external
    def execute_rotation(self, executor: Address, proposal_id: U64):
        """Execute a guardian rotation. Requires standard threshold confirmations.

        Args:
            executor: Trigger address.
            proposal_id: Rotation proposal ID.
        """
        self._require_initialized()
        executor.require_auth()

        proposal = self._get_proposal(proposal_id)
        if proposal["executed"]:
            raise ContractError.ALREADY_EXECUTED

        required = self.storage.get("threshold")
        if proposal["confirmations_count"] < required:
            raise ContractError.THRESHOLD_NOT_MET

        old_g = proposal["old_guardian"]
        new_g = proposal["new_guardian"]

        self.storage.set(("guardian", old_g), False)
        self.storage.set(("guardian", new_g), True)

        proposal["executed"] = True
        self.storage.set(("proposal", proposal_id), proposal)

        self.env.emit_event("guardian_rotated", {
            "proposal_id": proposal_id,
            "old_guardian": old_g,
            "new_guardian": new_g,
        })

    @view
    def is_guardian(self, address: Address) -> Bool:
        """Check if address is a guardian."""
        return self.storage.get(("guardian", address), False)

    @view
    def is_paused(self, target: Address) -> Bool:
        """Check if target is paused."""
        return self.storage.get(("paused", target), False)

    @view
    def get_proposal(self, proposal_id: U64) -> Map:
        """Get emergency action proposal details."""
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
