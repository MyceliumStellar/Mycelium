"""
Access Control — Hierarchical roles, admin overrides, and timelocked actions.

Mycelium Smart Contract for Stellar. Establishes a role-based access control (RBAC) hierarchy,
enforces custom safety timelocks on sensitive role assignments, provides membership lists,
and supports role renunciation.
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
    ALREADY_MEMBER = 5
    NOT_MEMBER = 6
    TIMELOCK_ACTIVE = 7
    TIMELOCK_NOT_EXPIRED = 8
    NO_REQUEST_ACTIVE = 9

@contract
class AccessControl:
    """
    Stellar access control ledger contract.
    Maintains user roles, administrative hierarchies, and timelocks for promotions.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, root_admin: Address):
        """
        Initialize the access control structure.
        Sets default administrative roles.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("initialized", True)
        self.storage.set("paused", False)

        # Role IDs: 0 = ROOT_ADMIN, 1 = MANAGER, 2 = OPERATOR
        # Assign root admin
        self._add_member_to_role(U64(0), root_admin)
        # ROOT_ADMIN manages manager and operator roles
        self.storage.set("role_admin_0", U64(0))
        self.storage.set("role_admin_1", U64(0))
        self.storage.set("role_admin_2", U64(0))

        # Setup default timelocks (0 seconds for operator, 1 hour for manager)
        self.storage.set("role_timelock_1", U64(3600))
        self.storage.set("role_timelock_2", U64(0))

        self.env.emit_event("initialized", {"root_admin": root_admin})

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause role requests."""
        caller.require_auth()
        self._require_initialized()
        self._require_role(U64(0), caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- ROLE MANAGEMENT CONFIGURATION ---

    @external
    def set_role_admin(self, caller: Address, role_id: U64, admin_role_id: U64):
        """
        Define which role has administrative authorization to grant/revoke memberships.
        """
        caller.require_auth()
        self._require_initialized()
        
        # Only admin of the current role admin can modify this
        curr_admin_role = self.storage.get(f"role_admin_{role_id}", U64(0))
        self._require_role(curr_admin_role, caller)

        self.storage.set(f"role_admin_{role_id}", admin_role_id)
        self.env.emit_event("role_admin_updated", {"role_id": role_id, "admin_role_id": admin_role_id})

    @external
    def set_role_timelock(self, caller: Address, role_id: U64, delay_sec: U64):
        """Set execution timelock delay for a role's promotion actions."""
        caller.require_auth()
        self._require_initialized()
        
        admin_role = self.storage.get(f"role_admin_{role_id}", U64(0))
        self._require_role(admin_role, caller)

        self.storage.set(f"role_timelock_{role_id}", delay_sec)
        self.env.emit_event("role_timelock_updated", {"role_id": role_id, "delay_sec": delay_sec})

    # --- MEMBER ROLES OPERATIONS ---

    @external
    def request_grant_role(self, caller: Address, role_id: U64, account: Address):
        """
        Propose/request adding a member to a role.
        Enforces a timelock delay if configured.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        admin_role = self.storage.get(f"role_admin_{role_id}", U64(0))
        self._require_role(admin_role, caller)

        # Check if already a member
        if self.storage.get(f"member_{role_id}_{account}", False):
            raise ContractError.ALREADY_MEMBER

        delay = self.storage.get(f"role_timelock_{role_id}", U64(0))
        if delay > U64(0):
            # Timelock required
            unlock_time = self._get_now() + delay
            self.storage.set(f"req_active_{role_id}_{account}", True)
            self.storage.set(f"req_unlock_{role_id}_{account}", unlock_time)
            
            self.env.emit_event("role_grant_proposed", {
                "role_id": role_id,
                "account": account,
                "unlock_time": unlock_time
            })
        else:
            # Immediate grant
            self._add_member_to_role(role_id, account)
            self.env.emit_event("role_granted", {"role_id": role_id, "account": account})

    @external
    def confirm_grant_role(self, caller: Address, role_id: U64, account: Address):
        """
        Confirm/finalize role grant after timelock expiry.
        """
        self._require_initialized()

        if not self.storage.get(f"req_active_{role_id}_{account}", False):
            raise ContractError.NO_REQUEST_ACTIVE

        unlock_time = self.storage.get(f"req_unlock_{role_id}_{account}", U64(0))
        if self._get_now() < unlock_time:
            raise ContractError.TIMELOCK_NOT_EXPIRED

        # Remove request
        self.storage.remove(f"req_active_{role_id}_{account}")
        self.storage.remove(f"req_unlock_{role_id}_{account}")

        # Grant role
        self._add_member_to_role(role_id, account)
        self.env.emit_event("role_granted", {"role_id": role_id, "account": account})

    @external
    def revoke_role(self, caller: Address, role_id: U64, account: Address):
        """Revoke role membership."""
        caller.require_auth()
        self._require_initialized()

        admin_role = self.storage.get(f"role_admin_{role_id}", U64(0))
        self._require_role(admin_role, caller)

        # Cannot revoke root admin role if they are the last one
        if role_id == U64(0):
            count = self.storage.get("role_members_count_0", U64(0))
            if count <= U64(1):
                raise ContractError.UNAUTHORIZED

        self._remove_member_from_role(role_id, account)
        self.env.emit_event("role_revoked", {"role_id": role_id, "account": account})

    @external
    def renounce_role(self, caller: Address, role_id: U64):
        """Voluntarily renounce role membership."""
        caller.require_auth()
        self._require_initialized()

        # Cannot renounce root admin role if last one
        if role_id == U64(0):
            count = self.storage.get("role_members_count_0", U64(0))
            if count <= U64(1):
                raise ContractError.UNAUTHORIZED

        self._remove_member_from_role(role_id, caller)
        self.env.emit_event("role_renounced", {"role_id": role_id, "account": caller})

    # --- VIEWS ---

    @view
    def has_role(self, role_id: U64, account: Address) -> Bool:
        """Check role membership."""
        self._require_initialized()
        return self.storage.get(f"member_{role_id}_{account}", False)

    @view
    def get_role_admin(self, role_id: U64) -> U64:
        """Query administrative role for role_id."""
        return self.storage.get(f"role_admin_{role_id}", U64(0))

    @view
    def get_role_members(self, role_id: U64) -> Vec:
        """Returns the complete list of members in a role."""
        self._require_initialized()
        members = Vec(self.env)
        count = self.storage.get(f"role_members_count_{role_id}", U64(0))
        for i in range(int(count)):
            m = self.storage.get(f"role_member_{role_id}_{i}")
            if m is not None:
                members.push_back(m)
        return members

    # --- INTERNAL HELPERS ---

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_not_paused(self):
        if self.storage.get("paused", False):
            raise ContractError.PAUSED

    def _require_role(self, role_id: U64, account: Address):
        if not self.storage.get(f"member_{role_id}_{account}", False):
            raise ContractError.UNAUTHORIZED

    def _get_now(self) -> U64:
        return self.env.ledger_timestamp()

    def _add_member_to_role(self, role_id: U64, account: Address):
        if self.storage.get(f"member_{role_id}_{account}", False):
            return

        self.storage.set(f"member_{role_id}_{account}", True)

        # Append to array list
        count = self.storage.get(f"role_members_count_{role_id}", U64(0))
        self.storage.set(f"role_member_{role_id}_{count}", account)
        self.storage.set(f"role_members_count_{role_id}", count + U64(1))

    def _remove_member_from_role(self, role_id: U64, account: Address):
        if not self.storage.get(f"member_{role_id}_{account}", False):
            raise ContractError.NOT_MEMBER

        self.storage.remove(f"member_{role_id}_{account}")

        # Clean array list
        count = self.storage.get(f"role_members_count_{role_id}", U64(0))
        for i in range(int(count)):
            m = self.storage.get(f"role_member_{role_id}_{i}")
            if m == account:
                last_idx = count - U64(1)
                last_member = self.storage.get(f"role_member_{role_id}_{last_idx}")
                
                # Replace with last element
                self.storage.set(f"role_member_{role_id}_{i}", last_member)
                self.storage.remove(f"role_member_{role_id}_{last_idx}")
                self.storage.set(f"role_members_count_{role_id}", last_idx)
                break
