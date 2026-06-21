"""
Payment Splitter — Split token payments among multiple payees according to shares.

Mycelium Smart Contract for Stellar
Allows specifying a group of payees and their respective share percentages (in bps).
Supports dynamic payee/share updates by admin, automatically settling any unclaimed
accumulated balances under the previous share structure before applying changes.
Uses a pull-payment pattern to prevent out-of-gas errors.
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
    NO_FUNDS_DUE = 5
    SHARES_MUST_TOTAL_10000 = 6
    PAYEE_NOT_FOUND = 7


@contract
class PaymentSplitter:
    """
    Distributes incoming tokens proportionately to a list of registered payees.
    Includes state preservation hooks for administrative payee weight reconfigurations.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, payees: Vec, shares: Vec):
        """Initialize the Payment Splitter with initial payees and shares."""
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("initialized", True)
        
        # Track active tokens list for settlement
        self.storage.set("token_count", U64(0))

        # Set payees
        self._set_payees_and_shares(payees, shares)

        self.env.emit_event("initialized", {
            "admin": admin,
            "payees_count": len(payees),
        })

    @external
    def deposit_payment(self, caller: Address, token: Address, amount: U128):
        """Deposit tokens to be split. Triggers internal accounting updates."""
        caller.require_auth()
        self._require_initialized()

        if amount == 0:
            raise ContractError.INVALID_PARAMETERS

        # Transfer tokens to this contract
        self.env.transfer(token, caller, self.env.current_contract(), amount)

        # Track active tokens
        self._register_token_if_new(token)

        # Update total received for this token
        received = self.storage.get(f"total_received:{token}", U128(0))
        self.storage.set(f"total_received:{token}", received + amount)

        self.env.emit_event("payment_received", {
            "token": token,
            "amount": amount,
            "sender": caller,
        })

    @external
    def release(self, payee: Address, token: Address):
        """Claim a payee's accrued share of a specific token."""
        self._require_initialized()

        if not self.storage.get(f"payee:exists:{payee}", False):
            raise ContractError.PAYEE_NOT_FOUND

        claimable = self._get_claimable_amount(payee, token)
        if claimable == 0:
            raise ContractError.NO_FUNDS_DUE

        # Update released tally
        released = self.storage.get(f"released:{payee}:{token}", U128(0))
        self.storage.set(f"released:{payee}:{token}", released + claimable)

        # In case there are settled funds, reduce them
        settled = self.storage.get(f"settled_claimable:{payee}:{token}", U128(0))
        if settled > 0:
            if claimable >= settled:
                self.storage.set(f"settled_claimable:{payee}:{token}", U128(0))
            else:
                self.storage.set(f"settled_claimable:{payee}:{token}", settled - claimable)

        # Transfer to payee
        self.env.transfer(token, self.env.current_contract(), payee, claimable)

        self.env.emit_event("payment_released", {
            "payee": payee,
            "token": token,
            "amount": claimable,
        })

    @external
    def update_payees(self, admin: Address, new_payees: Vec, new_shares: Vec):
        """Admin updates the payees list and share weights, first settling all current balances."""
        admin.require_auth()
        self._require_initialized()
        self._require_admin(admin)

        # 1. Settle all active tokens for existing payees
        self._settle_all_tokens()

        # 2. Clear old payee states
        self._clear_old_payee_shares()

        # 3. Set new payees and shares
        self._set_payees_and_shares(new_payees, new_shares)

        self.env.emit_event("payees_updated", {
            "new_payees_count": len(new_payees),
        })

    # ── View Functions ────────────────────────────────────────────────

    @view
    def get_payees(self) -> Vec:
        """Get the current list of payees."""
        payees = Vec()
        count = self.storage.get("payee_count", U64(0))
        for i in range(count):
            payee = self.storage.get(f"payee:index:{i}")
            payees.append(payee)
        return payees

    @view
    def get_payee_share(self, payee: Address) -> U64:
        """Get the share in basis points for a payee."""
        if not self.storage.get(f"payee:exists:{payee}", False):
            return U64(0)
        return self.storage.get(f"payee:share:{payee}", U64(0))

    @view
    def get_claimable(self, payee: Address, token: Address) -> U128:
        """Query outstanding claimable tokens for a payee."""
        return self._get_claimable_amount(payee, token)

    # ── Private Helpers ───────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _get_claimable_amount(self, payee: Address, token: Address) -> U128:
        # Claimable = Settled funds (from past share updates) + Current share of new funds
        settled = self.storage.get(f"settled_claimable:{payee}:{token}", U128(0))
        
        # If payee is not currently active, they only get settled funds
        if not self.storage.get(f"payee:exists:{payee}", False):
            return settled

        share = self.storage.get(f"payee:share:{payee}", U64(0))
        total_received = self.storage.get(f"total_received:{token}", U128(0))
        released = self.storage.get(f"released:{payee}:{token}", U128(0))

        current_due = (total_received * U128(share)) // U128(10000)
        
        # Deduct already released from current_due
        # Avoid underflow if payee share changed
        net_current_due = U128(0)
        if current_due > released:
            net_current_due = current_due - released

        return settled + net_current_due

    def _set_payees_and_shares(self, payees: Vec, shares: Vec):
        if len(payees) != len(shares) or len(payees) == 0:
            raise ContractError.INVALID_PARAMETERS

        total_shares = U64(0)
        for i in range(len(payees)):
            payee = payees[i]
            share = shares[i]
            if share == 0 or self.storage.get(f"payee:exists:{payee}", False):
                raise ContractError.INVALID_PARAMETERS
            total_shares = total_shares + share

        if total_shares != 10000:
            raise ContractError.SHARES_MUST_TOTAL_10000

        self.storage.set("payee_count", U64(len(payees)))
        for i in range(len(payees)):
            payee = payees[i]
            share = shares[i]
            self.storage.set(f"payee:index:{i}", payee)
            self.storage.set(f"payee:share:{payee}", share)
            self.storage.set(f"payee:exists:{payee}", True)

    def _clear_old_payee_shares(self):
        count = self.storage.get("payee_count", U64(0))
        for i in range(count):
            payee = self.storage.get(f"payee:index:{i}")
            self.storage.set(f"payee:share:{payee}", U64(0))
            self.storage.set(f"payee:exists:{payee}", False)
        self.storage.set("payee_count", U64(0))

    def _register_token_if_new(self, token: Address):
        if not self.storage.get(f"token:registered:{token}", False):
            self.storage.set(f"token:registered:{token}", True)
            count = self.storage.get("token_count", U64(0))
            self.storage.set(f"token:index:{count}", token)
            self.storage.set("token_count", count + U64(1))

    def _settle_all_tokens(self):
        token_count = self.storage.get("token_count", U64(0))
        payee_count = self.storage.get("payee_count", U64(0))

        for t in range(token_count):
            token = self.storage.get(f"token:index:{t}")
            
            for p in range(payee_count):
                payee = self.storage.get(f"payee:index:{p}")
                
                # Calculate what is due under current weights
                claimable = self._get_claimable_amount(payee, token)
                
                if claimable > 0:
                    # Move claimable into settled storage
                    self.storage.set(f"settled_claimable:{payee}:{token}", claimable)
                
                # Reset released tracking for the new configuration
                self.storage.set(f"released:{payee}:{token}", U128(0))
            
            # Reset total received baseline to start fresh for this token
            # Under new shares, only new deposits will accrue
            self.storage.set(f"total_received:{token}", U128(0))
