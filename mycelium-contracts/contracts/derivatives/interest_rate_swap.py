"""
Interest Rate Swap — Floating-to-fixed rate swap contracts with collateral margins and periodic settlements.

Mycelium Smart Contract for Stellar. Tracks interest rate swap agreements between two counterparties,
manages collateral margins in a designated ERC-20 like token, retrieves floating rates from an oracle,
calculates periodic net payment settlements, and handles contract termination/liquidations.
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
    INVALID_SWAP_PARAM = 5
    AGREEMENT_NOT_FOUND = 6
    AGREEMENT_EXPIRED = 7
    INSUFFICIENT_MARGIN = 8
    SETTLEMENT_NOT_DUE = 9
    ORACLE_READ_FAILED = 10
    ALREADY_SETTLED = 11
    TERMINATED = 12

@contract
class InterestRateSwap:
    """
    Interest Rate Swap contract representing a bilateral swap agreement.
    Fixed Rate Payer pays a fixed rate and receives a floating rate.
    Floating Rate Payer pays a floating rate and receives a fixed rate.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        oracle: Address,
        margin_token: Address
    ):
        """Initialize contract configuration parameters."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("oracle", oracle)
        self.storage.set("margin_token", margin_token)
        self.storage.set("agreement_nonce", U64(1))
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "oracle": oracle,
            "margin_token": margin_token
        })

    @external
    def create_swap_agreement(
        self,
        caller: Address,
        counterparty: Address,
        fixed_rate_bps: U64,
        floating_index: Symbol,
        notional_amount: U128,
        start_time: U64,
        end_time: U64,
        payment_interval: U64,
        initial_margin: U128
    ) -> U64:
        """
        Create a new interest rate swap agreement.
        Caller acts as the Fixed Rate Payer, Counterparty acts as the Floating Rate Payer.
        Both parties must deposit the initial margin to activate.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        now = self._get_now()
        if start_time < now or end_time <= start_time or payment_interval == U64(0):
            raise ContractError.INVALID_SWAP_PARAM

        if notional_amount == U128(0) or initial_margin == U128(0):
            raise ContractError.INVALID_SWAP_PARAM

        # Generate agreement ID
        nonce = self.storage.get("agreement_nonce", U64(1))
        self.storage.set("agreement_nonce", nonce + U64(1))

        # Transfer initial margin from caller
        token = self.storage.get("margin_token")
        contract_addr = self.env.current_contract_address()
        self.env.call(token, "transfer", caller, contract_addr, initial_margin)

        # Store agreement data
        prefix = f"swap_{nonce}_"
        self.storage.set(prefix + "fixed_payer", caller)
        self.storage.set(prefix + "floating_payer", counterparty)
        self.storage.set(prefix + "fixed_rate_bps", fixed_rate_bps)
        self.storage.set(prefix + "floating_index", floating_index)
        self.storage.set(prefix + "notional", notional_amount)
        self.storage.set(prefix + "start_time", start_time)
        self.storage.set(prefix + "end_time", end_time)
        self.storage.set(prefix + "payment_interval", payment_interval)
        self.storage.set(prefix + "last_settlement", start_time)
        
        # Collateral tracking
        self.storage.set(prefix + "fixed_margin", initial_margin)
        self.storage.set(prefix + "floating_margin", U128(0)) # Must be funded by counterparty
        self.storage.set(prefix + "status", Symbol("PENDING"))

        self.env.emit_event("swap_created", {
            "agreement_id": nonce,
            "fixed_payer": caller,
            "floating_payer": counterparty,
            "notional": notional_amount,
            "fixed_rate_bps": fixed_rate_bps
        })

        return nonce

    @external
    def accept_swap_agreement(
        self,
        caller: Address,
        agreement_id: U64,
        initial_margin: U128
    ):
        """Counterparty accepts the swap agreement and deposits initial margin."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        prefix = f"swap_{agreement_id}_"
        floating_payer = self.storage.get(prefix + "floating_payer")
        if floating_payer is None:
            raise ContractError.AGREEMENT_NOT_FOUND

        if caller != floating_payer:
            raise ContractError.UNAUTHORIZED

        status = self.storage.get(prefix + "status")
        if status != Symbol("PENDING"):
            raise ContractError.INVALID_SWAP_PARAM

        # Transfer margin from counterparty
        token = self.storage.get("margin_token")
        contract_addr = self.env.current_contract_address()
        self.env.call(token, "transfer", caller, contract_addr, initial_margin)

        # Set margin and update status
        self.storage.set(prefix + "floating_margin", initial_margin)
        self.storage.set(prefix + "status", Symbol("ACTIVE"))

        self.env.emit_event("swap_activated", {
            "agreement_id": agreement_id,
            "floating_payer": caller,
            "margin_deposited": initial_margin
        })

    @external
    def deposit_collateral(
        self,
        caller: Address,
        agreement_id: U64,
        amount: U128
    ):
        """Top up collateral margin for a specific swap agreement."""
        caller.require_auth()
        self._require_initialized()

        prefix = f"swap_{agreement_id}_"
        fixed_payer = self.storage.get(prefix + "fixed_payer")
        floating_payer = self.storage.get(prefix + "floating_payer")

        if fixed_payer is None:
            raise ContractError.AGREEMENT_NOT_FOUND

        token = self.storage.get("margin_token")
        contract_addr = self.env.current_contract_address()
        self.env.call(token, "transfer", caller, contract_addr, amount)

        if caller == fixed_payer:
            current = self.storage.get(prefix + "fixed_margin", U128(0))
            self.storage.set(prefix + "fixed_margin", current + amount)
        elif caller == floating_payer:
            current = self.storage.get(prefix + "floating_margin", U128(0))
            self.storage.set(prefix + "floating_margin", current + amount)
        else:
            raise ContractError.UNAUTHORIZED

        self.env.emit_event("collateral_deposited", {
            "agreement_id": agreement_id,
            "depositor": caller,
            "amount": amount
        })

    @external
    def settle_period(self, caller: Address, agreement_id: U64):
        """
        Settle the net interest difference for the elapsed interval.
        Anyone can call this, but typically executed by keepers or counterparties.
        """
        self._require_initialized()
        self._require_not_paused()

        prefix = f"swap_{agreement_id}_"
        status = self.storage.get(prefix + "status")
        if status != Symbol("ACTIVE"):
            raise ContractError.INVALID_SWAP_PARAM

        now = self._get_now()
        last_settlement = self.storage.get(prefix + "last_settlement", U64(0))
        interval = self.storage.get(prefix + "payment_interval", U64(0))
        end_time = self.storage.get(prefix + "end_time", U64(0))

        # Check if interval has elapsed or contract ended
        target_time = last_settlement + interval
        if now < target_time and now < end_time:
            raise ContractError.SETTLEMENT_NOT_DUE

        # Calculate time fraction (in seconds / year seconds)
        # Year length approximation: 31,536,000 seconds
        elapsed = now if now < end_time else end_time
        duration = elapsed - last_settlement
        if duration == U64(0):
            raise ContractError.ALREADY_SETTLED

        # Retrieve parameters
        notional = self.storage.get(prefix + "notional", U128(0))
        fixed_rate = self.storage.get(prefix + "fixed_rate_bps", U64(0))
        floating_index = self.storage.get(prefix + "floating_index")
        
        # Get floating rate from Oracle (in basis points)
        floating_rate = self._get_floating_rate(floating_index)

        # Accrued fixed payment: notional * fixed_rate * duration / (10000 * 31536000)
        fixed_pay = (notional * U128(fixed_rate) * U128(duration)) / U128(315_360_000_000)
        
        # Accrued floating payment: notional * floating_rate * duration / (10000 * 31536000)
        floating_pay = (notional * U128(floating_rate) * U128(duration)) / U128(315_360_000_000)

        # Net payment calculation
        # If fixed_pay > floating_pay: Fixed Payer pays net difference to Floating Payer.
        # If floating_pay > fixed_pay: Floating Payer pays net difference to Fixed Payer.
        net_diff = I128(0)
        fixed_margin = self.storage.get(prefix + "fixed_margin", U128(0))
        floating_margin = self.storage.get(prefix + "floating_margin", U128(0))

        if fixed_pay > floating_pay:
            diff = fixed_pay - floating_pay
            if fixed_margin < diff:
                # Default by Fixed Payer
                self._liquidate_swap(agreement_id, Symbol("FIXED_DEFAULT"))
                return
            
            # Transfer diff from fixed_margin to floating_margin
            self.storage.set(prefix + "fixed_margin", fixed_margin - diff)
            self.storage.set(prefix + "floating_margin", floating_margin + diff)
            net_diff = I128(int(diff))
        else:
            diff = floating_pay - fixed_pay
            if floating_margin < diff:
                # Default by Floating Payer
                self._liquidate_swap(agreement_id, Symbol("FLOATING_DEFAULT"))
                return

            # Transfer diff from floating_margin to fixed_margin
            self.storage.set(prefix + "floating_margin", floating_margin - diff)
            self.storage.set(prefix + "fixed_margin", fixed_margin + diff)
            net_diff = -I128(int(diff))

        # Update last settlement time
        self.storage.set(prefix + "last_settlement", elapsed)

        # If swap matured, change status
        if elapsed >= end_time:
            self.storage.set(prefix + "status", Symbol("MATURED"))
            self._refund_margins(agreement_id)

        self.env.emit_event("swap_settled", {
            "agreement_id": agreement_id,
            "duration": duration,
            "floating_rate": floating_rate,
            "net_difference": net_diff
        })

    @external
    def terminate_swap(self, caller: Address, agreement_id: U64):
        """Mutual termination or early termination if one party initiates and it is approved."""
        caller.require_auth()
        self._require_initialized()

        prefix = f"swap_{agreement_id}_"
        status = self.storage.get(prefix + "status")
        if status != Symbol("ACTIVE") and status != Symbol("PENDING"):
            raise ContractError.INVALID_SWAP_PARAM

        fixed_payer = self.storage.get(prefix + "fixed_payer")
        floating_payer = self.storage.get(prefix + "floating_payer")

        if caller != fixed_payer and caller != floating_payer:
            raise ContractError.UNAUTHORIZED

        # Mutual termination requires both to call. We record interest to terminate.
        if status == Symbol("PENDING"):
            # If still pending (not active), the creator can cancel anytime
            if caller == fixed_payer:
                self.storage.set(prefix + "status", Symbol("CANCELLED"))
                # Refund fixed payer
                margin = self.storage.get(prefix + "fixed_margin", U128(0))
                self.storage.set(prefix + "fixed_margin", U128(0))
                token = self.storage.get("margin_token")
                self.env.call(token, "transfer", self.env.current_contract_address(), fixed_payer, margin)
                
                self.env.emit_event("swap_terminated", {
                    "agreement_id": agreement_id,
                    "reason": Symbol("CREATOR_CANCELLED")
                })
            else:
                raise ContractError.UNAUTHORIZED
        else:
            # Active swaps: Record proposal
            proposal = self.storage.get(prefix + "terminate_proposal")
            if proposal is None:
                self.storage.set(prefix + "terminate_proposal", caller)
            else:
                if proposal != caller:
                    # Second party agrees. Terminate.
                    self.storage.set(prefix + "status", Symbol("TERMINATED"))
                    self._refund_margins(agreement_id)
                    self.env.emit_event("swap_terminated", {
                        "agreement_id": agreement_id,
                        "reason": Symbol("MUTUAL_AGREEMENT")
                    })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause settlement (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_agreement(self, agreement_id: U64) -> Map:
        """Query swap agreement parameters."""
        res = Map(self.env)
        prefix = f"swap_{agreement_id}_"
        fixed_payer = self.storage.get(prefix + "fixed_payer")
        if fixed_payer is not None:
            res.set("fixed_payer", fixed_payer)
            res.set("floating_payer", self.storage.get(prefix + "floating_payer"))
            res.set("fixed_rate_bps", self.storage.get(prefix + "fixed_rate_bps"))
            res.set("floating_index", self.storage.get(prefix + "floating_index"))
            res.set("notional", self.storage.get(prefix + "notional"))
            res.set("start_time", self.storage.get(prefix + "start_time"))
            res.set("end_time", self.storage.get(prefix + "end_time"))
            res.set("payment_interval", self.storage.get(prefix + "payment_interval"))
            res.set("last_settlement", self.storage.get(prefix + "last_settlement"))
            res.set("fixed_margin", self.storage.get(prefix + "fixed_margin"))
            res.set("floating_margin", self.storage.get(prefix + "floating_margin"))
            res.set("status", self.storage.get(prefix + "status"))
        return res

    # --- INTERNAL HELPERS ---

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_not_paused(self):
        if self.storage.get("paused", False):
            raise ContractError.PAUSED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _get_now(self) -> U64:
        return self.env.ledger_timestamp()

    def _get_floating_rate(self, index: Symbol) -> U64:
        """Call external Oracle to fetch current rate in basis points (e.g. 525 for 5.25%)."""
        oracle = self.storage.get("oracle")
        try:
            # Oracle interface get_rate(index: Symbol) -> U64
            return self.env.call(oracle, "get_rate", index)
        except Exception:
            raise ContractError.ORACLE_READ_FAILED

    def _liquidate_swap(self, agreement_id: U64, reason: Symbol):
        """In case of margin defaults, liquidation distributes remaining collateral."""
        prefix = f"swap_{agreement_id}_"
        fixed_payer = self.storage.get(prefix + "fixed_payer")
        floating_payer = self.storage.get(prefix + "floating_payer")
        fixed_margin = self.storage.get(prefix + "fixed_margin", U128(0))
        floating_margin = self.storage.get(prefix + "floating_margin", U128(0))
        
        token = self.storage.get("margin_token")
        contract_addr = self.env.current_contract_address()

        self.storage.set(prefix + "status", Symbol("LIQUIDATED"))

        # Send all remaining margin to the non-defaulting party
        if reason == Symbol("FIXED_DEFAULT"):
            total = fixed_margin + floating_margin
            self.storage.set(prefix + "fixed_margin", U128(0))
            self.storage.set(prefix + "floating_margin", U128(0))
            if total > U128(0):
                self.env.call(token, "transfer", contract_addr, floating_payer, total)
        else:
            total = fixed_margin + floating_margin
            self.storage.set(prefix + "fixed_margin", U128(0))
            self.storage.set(prefix + "floating_margin", U128(0))
            if total > U128(0):
                self.env.call(token, "transfer", contract_addr, fixed_payer, total)

        self.env.emit_event("swap_liquidated", {
            "agreement_id": agreement_id,
            "liquidator": reason
        })

    def _refund_margins(self, agreement_id: U64):
        """Refund remaining margin to both parties at termination/maturity."""
        prefix = f"swap_{agreement_id}_"
        fixed_payer = self.storage.get(prefix + "fixed_payer")
        floating_payer = self.storage.get(prefix + "floating_payer")
        fixed_margin = self.storage.get(prefix + "fixed_margin", U128(0))
        floating_margin = self.storage.get(prefix + "floating_margin", U128(0))

        token = self.storage.get("margin_token")
        contract_addr = self.env.current_contract_address()

        self.storage.set(prefix + "fixed_margin", U128(0))
        self.storage.set(prefix + "floating_margin", U128(0))

        if fixed_margin > U128(0):
            self.env.call(token, "transfer", contract_addr, fixed_payer, fixed_margin)
        if floating_margin > U128(0):
            self.env.call(token, "transfer", contract_addr, floating_payer, floating_margin)
