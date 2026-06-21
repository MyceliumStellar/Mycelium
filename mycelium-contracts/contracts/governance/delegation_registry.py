"""
Delegation Registry — Transitive delegations (A->B->C), circular delegation loop detection, partial delegations, and snapshot histories.

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
    INVALID_DELEGATION_BPS = 4
    CIRCULAR_DELEGATION = 5
    MAX_DEPTH_EXCEEDED = 6
    DELEGATE_NOT_FOUND = 7


@contract
class DelegationRegistry:
    """Registry supporting transitive, percentage-based, and historical vote power delegation."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, voting_token: Address):
        """Initialize the Delegation Registry.

        Args:
            admin: Admin address.
            voting_token: ERC-20 / Balance token address.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("token", voting_token)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "token": voting_token,
        })

    @external
    def delegate(
        self,
        delegator: Address,
        delegate: Address,
        percentage_bps: U64,
    ):
        """Delegate a percentage of voting power to a delegate.

        Args:
            delegator: Address delegating power.
            delegate: Target delegate.
            percentage_bps: Delegation amount in basis points (10000 = 100%).
        """
        self._require_initialized()
        delegator.require_auth()

        if percentage_bps > U64(10000):
            raise ContractError.INVALID_DELEGATION_BPS

        if delegator == delegate:
            raise ContractError.CIRCULAR_DELEGATION

        # 1. Circular detection: Check if delegate transitively delegates back to delegator
        if percentage_bps > U64(0):
            if self._has_path(delegate, delegator, U64(0)):
                raise ContractError.CIRCULAR_DELEGATION

        # 2. Update delegation representation
        old_bps = self.storage.get(("delegation_bps", delegator, delegate), U64(0))

        # Check total delegations from this delegator do not exceed 100% (10000 bps)
        total_delegated = self._get_total_delegated_bps(delegator)
        if (total_delegated - old_bps) + percentage_bps > U64(10000):
            raise ContractError.INVALID_DELEGATION_BPS

        self.storage.set(("delegation_bps", delegator, delegate), percentage_bps)

        # 3. Track delegate in delegator's list if new
        if old_bps == U64(0) and percentage_bps > U64(0):
            self._add_to_delegates(delegator, delegate)
            self._add_to_delegators(delegate, delegator)
        elif old_bps > U64(0) and percentage_bps == U64(0):
            self._remove_from_delegates(delegator, delegate)
            self._remove_from_delegators(delegate, delegator)

        # 4. Save to snapshot history
        now = self.env.ledger().timestamp()
        h_idx = self.storage.get(("history_count", delegator, delegate), U64(0))
        self.storage.set(("history", delegator, delegate, h_idx), {
            "bps": percentage_bps,
            "timestamp": now,
        })
        self.storage.set(("history_count", delegator, delegate), h_idx + U64(1))

        self.env.emit_event("delegation_updated", {
            "delegator": delegator,
            "delegate": delegate,
            "bps": percentage_bps,
            "timestamp": now,
        })

    @view
    def get_voting_power(self, account: Address) -> U128:
        """Calculate transitive voting power of an account (current)."""
        self._require_initialized()
        # To avoid loops, we track visited nodes in a temporary list or rely on circular prevention
        # Since we strictly prevent loops on write, a simple recursive traversal is safe.
        # Max traversal depth = 10
        return self._get_power_recursive(account, U64(0))

    @view
    def get_historical_delegation(
        self,
        delegator: Address,
        delegate: Address,
        timestamp: U64,
    ) -> U64:
        """Lookup historical delegation BPS at a given timestamp."""
        self._require_initialized()
        count = self.storage.get(("history_count", delegator, delegate), U64(0))
        if count == U64(0):
            return U64(0)

        # Reverse loop to find the latest state before or equal to timestamp
        for i in range(int(count)):
            rev_idx = count - U64(1) - U64(i)
            entry = self.storage.get(("history", delegator, delegate, rev_idx))
            if entry["timestamp"] <= timestamp:
                return entry["bps"]

        return U64(0)

    @view
    def get_delegations_info(self, delegator: Address) -> Map:
        """Get list of active delegates and BPS for a delegator."""
        delegates = Vec()
        count = self.storage.get(("delegates_count", delegator), U64(0))
        for i in range(count):
            d = self.storage.get(("delegate_at", delegator, U64(i)), None)
            if d is not None:
                bps = self.storage.get(("delegation_bps", delegator, d), U64(0))
                delegates.push_back({"delegate": d, "bps": bps})
        return {"delegates": delegates}

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _add_to_delegates(self, delegator: Address, delegate: Address):
        count = self.storage.get(("delegates_count", delegator), U64(0))
        self.storage.set(("delegate_at", delegator, count), delegate)
        self.storage.set(("delegates_count", delegator), count + U64(1))

    def _remove_from_delegates(self, delegator: Address, delegate: Address):
        count = self.storage.get(("delegates_count", delegator), U64(0))
        for i in range(count):
            d = self.storage.get(("delegate_at", delegator, U64(i)))
            if d == delegate:
                # Replace with last element and shrink
                last_d = self.storage.get(("delegate_at", delegator, count - U64(1)))
                self.storage.set(("delegate_at", delegator, U64(i)), last_d)
                self.storage.remove(("delegate_at", delegator, count - U64(1)))
                self.storage.set(("delegates_count", delegator), count - U64(1))
                break

    def _add_to_delegators(self, delegate: Address, delegator: Address):
        count = self.storage.get(("delegators_count", delegate), U64(0))
        self.storage.set(("delegator_at", delegate, count), delegator)
        self.storage.set(("delegators_count", delegate), count + U64(1))

    def _remove_from_delegators(self, delegate: Address, delegator: Address):
        count = self.storage.get(("delegators_count", delegate), U64(0))
        for i in range(count):
            d = self.storage.get(("delegator_at", delegate, U64(i)))
            if d == delegator:
                # Replace with last element and shrink
                last_d = self.storage.get(("delegator_at", delegate, count - U64(1)))
                self.storage.set(("delegator_at", delegate, U64(i)), last_d)
                self.storage.remove(("delegator_at", delegate, count - U64(1)))
                self.storage.set(("delegators_count", delegate), count - U64(1))
                break

    def _get_total_delegated_bps(self, delegator: Address) -> U64:
        count = self.storage.get(("delegates_count", delegator), U64(0))
        total = U64(0)
        for i in range(count):
            d = self.storage.get(("delegate_at", delegator, U64(i)), None)
            if d is not None:
                total = total + self.storage.get(("delegation_bps", delegator, d), U64(0))
        return total

    def _has_path(self, start: Address, target: Address, depth: U64) -> Bool:
        """Depth-first search path finder from start to target. Returns True if path exists."""
        if depth > U64(10):
            # Limit depth to avoid excessive resource usage
            return False

        if start == target:
            return True

        count = self.storage.get(("delegates_count", start), U64(0))
        for i in range(count):
            d = self.storage.get(("delegate_at", start, U64(i)), None)
            if d is not None:
                # Check if delegate is target or has path to target
                if self._has_path(d, target, depth + U64(1)):
                    return True
        return False

    def _get_power_recursive(self, account: Address, depth: U64) -> U128:
        """Accumulate voting power of the account recursively."""
        if depth > U64(10):
            raise ContractError.MAX_DEPTH_EXCEEDED

        # 1. Base balance: tokens held by account that are NOT delegated away
        token = self.storage.get("token")
        total_balance = self.env.invoke_contract(token, "balance", [account])

        total_delegated_bps = self._get_total_delegated_bps(account)
        delegated_away = (total_balance * U128(total_delegated_bps)) / U128(10000)
        remaining_power = total_balance - delegated_away

        # 2. Add power delegated TO this account from others
        delegators_count = self.storage.get(("delegators_count", account), U64(0))
        delegated_in = U128(0)

        for i in range(delegators_count):
            delegator = self.storage.get(("delegator_at", account, U64(i)), None)
            if delegator is not None:
                bps = self.storage.get(("delegation_bps", delegator, account), U64(0))
                # Recursively get delegator's total power and take their delegated share
                delegator_power = self._get_power_recursive(delegator, depth + U64(1))
                delegated_in = delegated_in + (delegator_power * U128(bps)) / U128(10000)

        return remaining_power + delegated_in
