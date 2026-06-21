"""
Soulbound Token (SBT) — Non-transferable credentials.

Mycelium Smart Contract for Stellar. Mints non-transferable SBTs, allows issuer revocation,
hosts social recovery via a multi-guardian threshold configuration, checks credential expiration,
and supports batch attestation.
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
    TOKEN_NOT_FOUND = 5
    TRANSFER_BLOCKED = 6
    EXPIRED = 7
    INVALID_GUARDIANS = 8
    RECOVERY_NOT_ACTIVE = 9
    ALREADY_VOTED = 10
    THRESHOLD_NOT_MET = 11
    SUPPLY_EXCEEDED = 12

@contract
class SoulboundToken:
    """
    A soulbound token contract representing non-transferable identity credentials or badges.
    Features admin revocation, guardian-based key recovery, and expirations.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, max_supply: U64):
        """Initialize the SBT contract."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("max_supply", max_supply)
        self.storage.set("next_token_id", U64(1))
        self.storage.set("total_supply", U64(0))
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin, "max_supply": max_supply})

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause minting/recovery actions."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- ISSUER OPERATIONS ---

    @external
    def mint(self, caller: Address, to: Address, expiry: U64) -> U64:
        """
        Mint a soulbound token to a specific recipient with an optional expiration timestamp.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()
        self._require_admin(caller)

        token_id = self._mint_internal(to, expiry)
        return token_id

    @external
    def batch_attest(self, caller: Address, recipients: Vec, expiries: Vec):
        """
        Batch attest (mint) soulbound tokens to multiple recipients.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()
        self._require_admin(caller)

        if len(recipients) != len(expiries) or len(recipients) == 0:
            raise ContractError.INVALID_GUARDIANS

        for i in range(len(recipients)):
            to = recipients.get(i)
            expiry = expiries.get(i)
            self._mint_internal(to, expiry)

    @external
    def revoke(self, caller: Address, token_id: U64):
        """
        Issuer revokes (burns) an active soulbound token.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        owner = self._require_exists(token_id)

        # Clear token parameters
        self.storage.remove(f"owner_{token_id}")
        self.storage.remove(f"expiry_{token_id}")
        self._cleanup_recovery_state(token_id)

        # Update supply and balance
        curr_supply = self.storage.get("total_supply", U64(0))
        if curr_supply > U64(0):
            self.storage.set("total_supply", curr_supply - U64(1))

        owner_bal = self.storage.get(f"balance_{owner}", U64(0))
        if owner_bal > U64(0):
            self.storage.set(f"balance_{owner}", owner_bal - U64(1))

        self.env.emit_event("revoked", {"token_id": token_id, "former_owner": owner})

    # --- SOULBOUND RESTRICTION ---

    @external
    def transfer(self, caller: Address, to: Address, token_id: U64):
        """
        Overridden transfer function. SBTs are soulbound and cannot be transferred.
        """
        raise ContractError.TRANSFER_BLOCKED

    # --- SOCIAL RECOVERY SYSTEM ---

    @external
    def set_guardians(self, caller: Address, token_id: U64, guardians: Vec, threshold: U64):
        """
        Set or update social recovery guardians for an SBT.
        Can only be called by the token owner.
        """
        caller.require_auth()
        self._require_initialized()

        owner = self._require_exists(token_id)
        if caller != owner:
            raise ContractError.UNAUTHORIZED

        num_guardians = len(guardians)
        if num_guardians == 0 or threshold == U64(0) or threshold > num_guardians:
            raise ContractError.INVALID_GUARDIANS

        # Save guardians
        self.storage.set(f"guardians_len_{token_id}", num_guardians)
        self.storage.set(f"recovery_threshold_{token_id}", threshold)

        for i in range(num_guardians):
            self.storage.set(f"guardian_{token_id}_{i}", guardians.get(i))

        # Reset any active recovery
        self._cleanup_recovery_state(token_id)

        self.env.emit_event("guardians_configured", {
            "token_id": token_id,
            "threshold": threshold,
            "count": num_guardians
        })

    @external
    def initiate_recovery(self, caller: Address, token_id: U64, new_owner: Address):
        """
        A registered guardian initiates a social recovery request to migrate the SBT to a new address.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_exists(token_id)

        # Verify caller is a guardian
        self._require_guardian(token_id, caller)

        if self.storage.get(f"recovery_active_{token_id}", False):
            raise ContractError.RECOVERY_NOT_ACTIVE

        # Start recovery
        self.storage.set(f"recovery_active_{token_id}", True)
        self.storage.set(f"recovery_proposed_owner_{token_id}", new_owner)
        self.storage.set(f"recovery_votes_{token_id}", U64(1))
        self.storage.set(f"recovery_voted_{token_id}_{caller}", True)

        self.env.emit_event("recovery_initiated", {
            "token_id": token_id,
            "proposed_owner": new_owner,
            "initiator": caller
        })

        # Auto-check if threshold is 1
        self._check_and_execute_recovery(token_id)

    @external
    def support_recovery(self, caller: Address, token_id: U64):
        """
        Another guardian votes to support/confirm the active recovery.
        Executes key migration if threshold is met.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_exists(token_id)

        self._require_guardian(token_id, caller)

        if not self.storage.get(f"recovery_active_{token_id}", False):
            raise ContractError.RECOVERY_NOT_ACTIVE

        if self.storage.get(f"recovery_voted_{token_id}_{caller}", False):
            raise ContractError.ALREADY_VOTED

        self.storage.set(f"recovery_voted_{token_id}_{caller}", True)
        votes = self.storage.get(f"recovery_votes_{token_id}", U64(0)) + U64(1)
        self.storage.set(f"recovery_votes_{token_id}", votes)

        self.env.emit_event("recovery_supported", {"token_id": token_id, "guardian": caller})

        self._check_and_execute_recovery(token_id)

    @external
    def cancel_recovery(self, caller: Address, token_id: U64):
        """
        The current owner of the SBT cancels the recovery proposal if they still have access.
        """
        caller.require_auth()
        self._require_initialized()

        owner = self._require_exists(token_id)
        if caller != owner:
            raise ContractError.UNAUTHORIZED

        self._cleanup_recovery_state(token_id)
        self.env.emit_event("recovery_cancelled", {"token_id": token_id})

    # --- VIEWS ---

    @view
    def is_valid(self, token_id: U64) -> Bool:
        """Checks if SBT exists and is not expired."""
        owner = self.storage.get(f"owner_{token_id}")
        if owner is None:
            return False

        expiry = self.storage.get(f"expiry_{token_id}", U64(0))
        if expiry > U64(0) and self._get_now() > expiry:
            return False

        return True

    @view
    def get_token_owner(self, token_id: U64) -> Address:
        """Returns owner if token is valid, else raises error."""
        self._require_initialized()
        if not self.is_valid(token_id):
            raise ContractError.EXPIRED
        return self.storage.get(f"owner_{token_id}")

    @view
    def get_recovery_status(self, token_id: U64) -> Map:
        """View recovery proposal details."""
        res = Map(self.env)
        active = self.storage.get(f"recovery_active_{token_id}", False)
        res.set("active", active)
        if active:
            res.set("proposed_owner", self.storage.get(f"recovery_proposed_owner_{token_id}"))
            res.set("votes", self.storage.get(f"recovery_votes_{token_id}"))
            res.set("threshold", self.storage.get(f"recovery_threshold_{token_id}"))
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

    def _require_exists(self, token_id: U64) -> Address:
        owner = self.storage.get(f"owner_{token_id}")
        if owner is None:
            raise ContractError.TOKEN_NOT_FOUND
        return owner

    def _get_now(self) -> U64:
        return self.env.ledger_timestamp()

    def _mint_internal(self, to: Address, expiry: U64) -> U64:
        next_id = self.storage.get("next_token_id", U64(1))
        max_supply = self.storage.get("max_supply", U64(0))
        if next_id > max_supply:
            raise ContractError.SUPPLY_EXCEEDED

        self.storage.set(f"owner_{next_id}", to)
        if expiry > U64(0):
            self.storage.set(f"expiry_{next_id}", expiry)

        self.storage.set("next_token_id", next_id + U64(1))
        curr_supply = self.storage.get("total_supply", U64(0))
        self.storage.set("total_supply", curr_supply + U64(1))

        owner_bal = self.storage.get(f"balance_{to}", U64(0))
        self.storage.set(f"balance_{to}", owner_bal + U64(1))

        self.env.emit_event("minted", {"token_id": next_id, "to": to})
        return next_id

    def _require_guardian(self, token_id: U64, caller: Address):
        length = self.storage.get(f"guardians_len_{token_id}", U64(0))
        found = False
        for i in range(int(length)):
            g = self.storage.get(f"guardian_{token_id}_{i}")
            if g == caller:
                found = True
                break
        if not found:
            raise ContractError.UNAUTHORIZED

    def _cleanup_recovery_state(self, token_id: U64):
        self.storage.remove(f"recovery_active_{token_id}")
        self.storage.remove(f"recovery_proposed_owner_{token_id}")
        self.storage.remove(f"recovery_votes_{token_id}")
        
        # Clear voting flags
        length = self.storage.get(f"guardians_len_{token_id}", U64(0))
        for i in range(int(length)):
            g = self.storage.get(f"guardian_{token_id}_{i}")
            self.storage.remove(f"recovery_voted_{token_id}_{g}")

    def _check_and_execute_recovery(self, token_id: U64):
        votes = self.storage.get(f"recovery_votes_{token_id}", U64(0))
        threshold = self.storage.get(f"recovery_threshold_{token_id}", U64(0))

        if votes >= threshold and threshold > U64(0):
            old_owner = self.storage.get(f"owner_{token_id}")
            new_owner = self.storage.get(f"recovery_proposed_owner_{token_id}")

            # Reassign token ownership
            self.storage.set(f"owner_{token_id}", new_owner)

            # Update balances
            old_bal = self.storage.get(f"balance_{old_owner}", U64(0))
            if old_bal > U64(0):
                self.storage.set(f"balance_{old_owner}", old_bal - U64(1))

            new_bal = self.storage.get(f"balance_{new_owner}", U64(0))
            self.storage.set(f"balance_{new_owner}", new_bal + U64(1))

            # Cleanup recovery state
            self._cleanup_recovery_state(token_id)

            self.env.emit_event("recovery_completed", {
                "token_id": token_id,
                "old_owner": old_owner,
                "new_owner": new_owner
            })
