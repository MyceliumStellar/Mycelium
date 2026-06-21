"""
Social Recovery — M-of-N Guardian recovery with timelocks and cancellations.

Mycelium Smart Contract for Stellar. Allows accounts to register recovery guardians,
propose recovery migrations, collect guardian approvals, enforce safety timelocks,
and permit the original owner to cancel unauthorized recovery attempts.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)

class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    PAUSED = 4
    INVALID_GUARDIANS = 5
    NO_PROPOSAL_ACTIVE = 6
    PROPOSAL_ACTIVE = 7
    TIMELOCK_NOT_EXPIRED = 8
    THRESHOLD_NOT_MET = 9
    ALREADY_VOTED = 10
    TIMELOCK_ACTIVE = 11

@contract
class SocialRecovery:
    """
    Dedicated social recovery manager.
    Protects user keys by routing recovery attempts through a cohort of trusted guardians.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """Initialize the social recovery vault."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin})

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause recovery proposals."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- OWNER CONFIGURATION ---

    @external
    def configure_recovery(
        self,
        caller: Address,
        guardians: Vec,
        threshold: U64,
        timelock_delay_sec: U64
    ):
        """
        Configure or update the social recovery setup for the calling address.
        
        Args:
            caller: The owner address configuration is being set for.
            guardians: Trusted guardian addresses.
            threshold: Minimum guardian approvals (M of N).
            timelock_delay_sec: Safety delay before execution (e.g. 3 days = 259200).
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        num_guardians = len(guardians)
        if num_guardians == 0 or threshold == U64(0) or threshold > num_guardians:
            raise ContractError.INVALID_GUARDIANS

        # Guard against editing setup during active recovery (forces cancellation first)
        if self.storage.get(f"proposal_active_{caller}", False):
            raise ContractError.PROPOSAL_ACTIVE

        # Save configuration
        self.storage.set(f"guardians_len_{caller}", num_guardians)
        self.storage.set(f"threshold_{caller}", threshold)
        self.storage.set(f"timelock_{caller}", timelock_delay_sec)

        for i in range(num_guardians):
            self.storage.set(f"guardian_{caller}_{i}", guardians.get(i))

        self.env.emit_event("recovery_configured", {
            "owner": caller,
            "threshold": threshold,
            "guardians_count": num_guardians,
            "timelock": timelock_delay_sec
        })

    # --- SOCIAL RECOVERY OPERATIONS ---

    @external
    def propose_recovery(self, caller: Address, target_owner: Address, proposed_new_key: Address):
        """
        Initiate a recovery proposal. Must be called by one of target_owner's registered guardians.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        # Check that target has recovery configured
        threshold = self.storage.get(f"threshold_{target_owner}", U64(0))
        if threshold == U64(0):
            raise ContractError.INVALID_GUARDIANS

        # Check caller is a guardian
        self._require_guardian(target_owner, caller)

        # Check no active proposal
        if self.storage.get(f"proposal_active_{target_owner}", False):
            raise ContractError.PROPOSAL_ACTIVE

        # Setup proposal
        self.storage.set(f"proposal_active_{target_owner}", True)
        self.storage.set(f"proposed_new_key_{target_owner}", proposed_new_key)
        self.storage.set(f"proposal_votes_{target_owner}", U64(1))
        self.storage.set(f"proposal_voted_{target_owner}_{caller}", True)
        self.storage.set(f"proposal_start_{target_owner}", self._get_now())
        self.storage.set(f"proposal_timelock_started_{target_owner}", False)

        self.env.emit_event("recovery_proposed", {
            "owner": target_owner,
            "proposed_new_key": proposed_new_key,
            "proposer": caller
        })

        # Auto-activate timelock if threshold is 1
        self._evaluate_proposal_votes(target_owner)

    @external
    def accept_proposal(self, caller: Address, target_owner: Address):
        """
        Approve/vote for the active recovery proposal.
        Caller must be a guardian who hasn't voted yet.
        """
        caller.require_auth()
        self._require_initialized()

        if not self.storage.get(f"proposal_active_{target_owner}", False):
            raise ContractError.NO_PROPOSAL_ACTIVE

        self._require_guardian(target_owner, caller)

        if self.storage.get(f"proposal_voted_{target_owner}_{caller}", False):
            raise ContractError.ALREADY_VOTED

        self.storage.set(f"proposal_voted_{target_owner}_{caller}", True)
        votes = self.storage.get(f"proposal_votes_{target_owner}", U64(0)) + U64(1)
        self.storage.set(f"proposal_votes_{target_owner}", votes)

        self.env.emit_event("recovery_accepted", {
            "owner": target_owner,
            "guardian": caller,
            "total_votes": votes
        })

        self._evaluate_proposal_votes(target_owner)

    @external
    def cancel_proposal(self, caller: Address):
        """
        Original owner cancels the active recovery proposal.
        Can be done at any point during voting or timelock.
        """
        caller.require_auth()
        self._require_initialized()

        if not self.storage.get(f"proposal_active_{caller}", False):
            raise ContractError.NO_PROPOSAL_ACTIVE

        self._cleanup_proposal_state(caller)
        self.env.emit_event("recovery_cancelled", {"owner": caller})

    @external
    def execute_recovery(self, caller: Address, target_owner: Address):
        """
        Commit the recovery migration. Anyone can execute once the threshold
        is reached and the safety timelock has elapsed.
        """
        self._require_initialized()

        if not self.storage.get(f"proposal_active_{target_owner}", False):
            raise ContractError.NO_PROPOSAL_ACTIVE

        # Check if timelock started
        if not self.storage.get(f"proposal_timelock_started_{target_owner}", False):
            raise ContractError.THRESHOLD_NOT_MET

        unlock_time = self.storage.get(f"proposal_unlock_time_{target_owner}", U64(0))
        if self._get_now() < unlock_time:
            raise ContractError.TIMELOCK_NOT_EXPIRED

        new_key = self.storage.get(f"proposed_new_key_{target_owner}")

        # Clean up recovery configuration and proposal parameters
        self._cleanup_proposal_state(target_owner)
        self._cleanup_recovery_setup(target_owner)

        # Emit migration event indicating target_owner identity transferred to new_key
        self.env.emit_event("recovery_executed", {
            "old_owner": target_owner,
            "new_owner": new_key
        })

    # --- VIEWS ---

    @view
    def get_recovery_setup(self, owner: Address) -> Map:
        """Returns M-of-N setup details."""
        res = Map(self.env)
        threshold = self.storage.get(f"threshold_{owner}", U64(0))
        if threshold > U64(0):
            res.set("threshold", threshold)
            res.set("guardians_count", self.storage.get(f"guardians_len_{owner}"))
            res.set("timelock", self.storage.get(f"timelock_{owner}"))
        return res

    @view
    def get_proposal_details(self, owner: Address) -> Map:
        """Inspect active proposal status, votes, and timelock expiry."""
        res = Map(self.env)
        active = self.storage.get(f"proposal_active_{owner}", False)
        res.set("active", active)
        if active:
            res.set("proposed_key", self.storage.get(f"proposed_new_key_{owner}"))
            res.set("votes", self.storage.get(f"proposal_votes_{owner}"))
            res.set("timelock_started", self.storage.get(f"proposal_timelock_started_{owner}"))
            res.set("unlock_time", self.storage.get(f"proposal_unlock_time_{owner}", U64(0)))
        return res

    # --- INTERNAL HELPERS ---

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_not_paused(self):
        if self.storage.get("paused", False):
            raise ContractError.PAUSED

    def _require_admin(self, caller: Address):
        if caller != self.storage.get("admin"):
            raise ContractError.UNAUTHORIZED

    def _get_now(self) -> U64:
        return self.env.ledger_timestamp()

    def _require_guardian(self, target_owner: Address, caller: Address):
        length = self.storage.get(f"guardians_len_{target_owner}", U64(0))
        found = False
        for i in range(int(length)):
            if self.storage.get(f"guardian_{target_owner}_{i}") == caller:
                found = True
                break
        if not found:
            raise ContractError.UNAUTHORIZED

    def _cleanup_proposal_state(self, owner: Address):
        self.storage.remove(f"proposal_active_{owner}")
        self.storage.remove(f"proposed_new_key_{owner}")
        self.storage.remove(f"proposal_votes_{owner}")
        self.storage.remove(f"proposal_start_{owner}")
        self.storage.remove(f"proposal_timelock_started_{owner}")
        self.storage.remove(f"proposal_unlock_time_{owner}")

        # Clear voted markers
        length = self.storage.get(f"guardians_len_{owner}", U64(0))
        for i in range(int(length)):
            g = self.storage.get(f"guardian_{owner}_{i}")
            self.storage.remove(f"proposal_voted_{owner}_{g}")

    def _cleanup_recovery_setup(self, owner: Address):
        length = self.storage.get(f"guardians_len_{owner}", U64(0))
        for i in range(int(length)):
            self.storage.remove(f"guardian_{owner}_{i}")
        self.storage.remove(f"guardians_len_{owner}")
        self.storage.remove(f"threshold_{owner}")
        self.storage.remove(f"timelock_{owner}")

    def _evaluate_proposal_votes(self, owner: Address):
        """Check if voting threshold is met and initiate safety timelock."""
        votes = self.storage.get(f"proposal_votes_{owner}", U64(0))
        threshold = self.storage.get(f"threshold_{owner}", U64(0))
        timelock_started = self.storage.get(f"proposal_timelock_started_{owner}", False)

        if votes >= threshold and not timelock_started:
            delay = self.storage.get(f"timelock_{owner}", U64(0))
            unlock_time = self._get_now() + delay
            
            self.storage.set(f"proposal_timelock_started_{owner}", True)
            self.storage.set(f"proposal_unlock_time_{owner}", unlock_time)

            self.env.emit_event("recovery_timelock_started", {
                "owner": owner,
                "unlock_time": unlock_time
            })
