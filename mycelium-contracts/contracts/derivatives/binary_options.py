"""
Binary Options — All-or-nothing payoff structures, strike pricing, oracle settlements, and dispute arbitration.

Mycelium Smart Contract for Stellar. Enables trading of binary (call/put) options where buyers buy contracts
from writers. The outcome is settled using oracle price inputs at expiry. Includes a dispute window during which
parties can dispute the outcome, triggering a manual arbitration process by a trusted arbitrator.
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
    INVALID_PARAM = 5
    OPTION_NOT_FOUND = 6
    EXPIRED = 7
    NOT_EXPIRED = 8
    INVALID_STATUS = 9
    DISPUTE_WINDOW_EXPIRED = 10
    NO_DISPUTE_ACTIVE = 11
    ORACLE_READ_FAILED = 12

@contract
class BinaryOptions:
    """
    Binary Options Contract supporting strike-based cash-or-nothing options (Call/Put).
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        oracle: Address,
        stablecoin: Address,
        arbitrator: Address,
        dispute_window: U64 # Duration in seconds, e.g. 86400 for 24h
    ):
        """Initialize contract configurations."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("oracle", oracle)
        self.storage.set("stablecoin", stablecoin)
        self.storage.set("arbitrator", arbitrator)
        self.storage.set("dispute_window", dispute_window)
        self.storage.set("option_nonce", U64(1))
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "oracle": oracle,
            "arbitrator": arbitrator,
            "dispute_window": dispute_window
        })

    @external
    def create_option_offer(
        self,
        buyer: Address,
        underlying: Symbol,
        is_call: Bool,
        strike_price: U128,
        expiry: U64,
        premium: U128,
        payoff_amount: U128
    ) -> U64:
        """
        Buyer creates an option offer, depositing the premium.
        A writer must take the offer to make it active, depositing (payoff_amount - premium).
        """
        buyer.require_auth()
        self._require_initialized()
        self._require_not_paused()

        now = self._get_now()
        if expiry <= now or payoff_amount <= premium or premium == U128(0):
            raise ContractError.INVALID_PARAM

        # Take premium from buyer
        stablecoin = self.storage.get("stablecoin")
        contract_addr = self.env.current_contract_address()
        self.env.call(stablecoin, "transfer", buyer, contract_addr, premium)

        # Nonce
        option_id = self.storage.get("option_nonce", U64(1))
        self.storage.set("option_nonce", option_id + U64(1))

        # Store details
        prefix = f"opt_{option_id}_"
        self.storage.set(prefix + "buyer", buyer)
        self.storage.set(prefix + "writer", Address(contract_addr)) # Temporarily contract until filled
        self.storage.set(prefix + "underlying", underlying)
        self.storage.set(prefix + "is_call", is_call)
        self.storage.set(prefix + "strike_price", strike_price)
        self.storage.set(prefix + "expiry", expiry)
        self.storage.set(prefix + "premium", premium)
        self.storage.set(prefix + "payoff", payoff_amount)
        self.storage.set(prefix + "status", Symbol("OFFERED"))

        self.env.emit_event("option_offered", {
            "option_id": option_id,
            "buyer": buyer,
            "underlying": underlying,
            "strike_price": strike_price,
            "expiry": expiry,
            "premium": premium,
            "payoff": payoff_amount
        })

        return option_id

    @external
    def write_option(self, writer: Address, option_id: U64):
        """Writer accepts the option offer and deposits the remaining payoff balance."""
        writer.require_auth()
        self._require_initialized()
        self._require_not_paused()

        prefix = f"opt_{option_id}_"
        status = self.storage.get(prefix + "status")
        if status is None:
            raise ContractError.OPTION_NOT_FOUND
        if status != Symbol("OFFERED"):
            raise ContractError.INVALID_STATUS

        expiry = self.storage.get(prefix + "expiry", U64(0))
        if expiry <= self._get_now():
            raise ContractError.EXPIRED

        payoff = self.storage.get(prefix + "payoff", U128(0))
        premium = self.storage.get(prefix + "premium", U128(0))
        required_collateral = payoff - premium

        # Transfer collateral from writer
        stablecoin = self.storage.get("stablecoin")
        contract_addr = self.env.current_contract_address()
        self.env.call(stablecoin, "transfer", writer, contract_addr, required_collateral)

        # Update option state
        self.storage.set(prefix + "writer", writer)
        self.storage.set(prefix + "status", Symbol("ACTIVE"))

        self.env.emit_event("option_active", {
            "option_id": option_id,
            "writer": writer
        })

    @external
    def cancel_offer(self, caller: Address, option_id: U64):
        """Cancel the offered option before a writer accepts it and retrieve the premium."""
        caller.require_auth()
        self._require_initialized()

        prefix = f"opt_{option_id}_"
        status = self.storage.get(prefix + "status")
        if status != Symbol("OFFERED"):
            raise ContractError.INVALID_STATUS

        buyer = self.storage.get(prefix + "buyer")
        if caller != buyer:
            raise ContractError.UNAUTHORIZED

        premium = self.storage.get(prefix + "premium", U128(0))
        self.storage.set(prefix + "status", Symbol("CANCELLED"))

        # Refund buyer
        stablecoin = self.storage.get("stablecoin")
        self.env.call(stablecoin, "transfer", self.env.current_contract_address(), buyer, premium)

        self.env.emit_event("option_cancelled", {
            "option_id": option_id
        })

    @external
    def settle_option(self, option_id: U64):
        """Settle option based on oracle price at expiry."""
        self._require_initialized()
        self._require_not_paused()

        prefix = f"opt_{option_id}_"
        status = self.storage.get(prefix + "status")
        if status != Symbol("ACTIVE"):
            raise ContractError.INVALID_STATUS

        expiry = self.storage.get(prefix + "expiry", U64(0))
        if self._get_now() < expiry:
            raise ContractError.NOT_EXPIRED

        underlying = self.storage.get(prefix + "underlying")
        
        # Get price at expiry (or current price if exact timestamp history is not available)
        price = self._get_oracle_price(underlying)

        strike = self.storage.get(prefix + "strike_price", U128(0))
        is_call = self.storage.get(prefix + "is_call", False)

        # Determine winner
        # Call wins if price >= strike. Put wins if price <= strike.
        is_won = False
        if is_call:
            is_won = (price >= strike)
        else:
            is_won = (price <= strike)

        self.storage.set(prefix + "settled_price", price)
        self.storage.set(prefix + "buyer_won", is_won)
        self.storage.set(prefix + "status", Symbol("SETTLED"))
        self.storage.set(prefix + "settlement_time", self._get_now())

        self.env.emit_event("option_settled", {
            "option_id": option_id,
            "final_price": price,
            "buyer_won": is_won
        })

    @external
    def dispute_outcome(self, caller: Address, option_id: U64):
        """Buyer or writer can dispute the settlement within the dispute window."""
        caller.require_auth()
        self._require_initialized()

        prefix = f"opt_{option_id}_"
        status = self.storage.get(prefix + "status")
        if status != Symbol("SETTLED"):
            raise ContractError.INVALID_STATUS

        settlement_time = self.storage.get(prefix + "settlement_time", U64(0))
        dispute_window = self.storage.get("dispute_window", U64(0))

        if self._get_now() > settlement_time + dispute_window:
            raise ContractError.DISPUTE_WINDOW_EXPIRED

        buyer = self.storage.get(prefix + "buyer")
        writer = self.storage.get(prefix + "writer")
        if caller != buyer and caller != writer:
            raise ContractError.UNAUTHORIZED

        self.storage.set(prefix + "status", Symbol("DISPUTED"))
        self.storage.set(prefix + "disputer", caller)

        self.env.emit_event("option_disputed", {
            "option_id": option_id,
            "disputer": caller
        })

    @external
    def resolve_dispute(self, caller: Address, option_id: U64, buyer_won: Bool):
        """Arbitrator resolves the dispute, making the final decision."""
        caller.require_auth()
        self._require_initialized()

        arbitrator = self.storage.get("arbitrator")
        if caller != arbitrator:
            raise ContractError.UNAUTHORIZED

        prefix = f"opt_{option_id}_"
        status = self.storage.get(prefix + "status")
        if status != Symbol("DISPUTED"):
            raise ContractError.NO_DISPUTE_ACTIVE

        self.storage.set(prefix + "buyer_won", buyer_won)
        self.storage.set(prefix + "status", Symbol("RESOLVED"))

        self.env.emit_event("dispute_resolved", {
            "option_id": option_id,
            "buyer_won": buyer_won
        })

    @external
    def claim_payout(self, caller: Address, option_id: U64):
        """Winner claims the option payoff after settlement or dispute resolution."""
        caller.require_auth()
        self._require_initialized()

        prefix = f"opt_{option_id}_"
        status = self.storage.get(prefix + "status")

        buyer = self.storage.get(prefix + "buyer")
        writer = self.storage.get(prefix + "writer")

        # Claim can occur when SETTLED (after dispute window has passed) or when RESOLVED
        if status == Symbol("SETTLED"):
            settlement_time = self.storage.get(prefix + "settlement_time", U64(0))
            dispute_window = self.storage.get("dispute_window", U64(0))
            if self._get_now() <= settlement_time + dispute_window:
                raise ContractError.INVALID_STATUS # Still inside dispute window
        elif status != Symbol("RESOLVED"):
            raise ContractError.INVALID_STATUS

        buyer_won = self.storage.get(prefix + "buyer_won", False)
        payoff = self.storage.get(prefix + "payoff", U128(0))

        # Check who is authorized to claim
        recipient = buyer if buyer_won else writer
        if caller != recipient:
            raise ContractError.UNAUTHORIZED

        # Complete claim by removing balance
        self.storage.set(prefix + "payoff", U128(0))
        self.storage.set(prefix + "status", Symbol("CLAIMED"))

        # Pay
        stablecoin = self.storage.get("stablecoin")
        self.env.call(stablecoin, "transfer", self.env.current_contract_address(), caller, payoff)

        self.env.emit_event("payoff_claimed", {
            "option_id": option_id,
            "recipient": caller,
            "amount": payoff
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause contract operations (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_option_details(self, option_id: U64) -> Map:
        """Query option parameters and status."""
        res = Map(self.env)
        prefix = f"opt_{option_id}_"
        buyer = self.storage.get(prefix + "buyer")
        if buyer is not None:
            res.set("buyer", buyer)
            res.set("writer", self.storage.get(prefix + "writer"))
            res.set("underlying", self.storage.get(prefix + "underlying"))
            res.set("is_call", self.storage.get(prefix + "is_call"))
            res.set("strike_price", self.storage.get(prefix + "strike_price"))
            res.set("expiry", self.storage.get(prefix + "expiry"))
            res.set("premium", self.storage.get(prefix + "premium"))
            res.set("payoff", self.storage.get(prefix + "payoff"))
            res.set("status", self.storage.get(prefix + "status"))
            res.set("settled_price", self.storage.get(prefix + "settled_price"))
            res.set("buyer_won", self.storage.get(prefix + "buyer_won"))
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

    def _get_oracle_price(self, asset: Symbol) -> U128:
        """Call external Oracle to fetch current price."""
        oracle = self.storage.get("oracle")
        try:
            # Expected signature on oracle: get_price(asset: Symbol) -> U128
            return self.env.call(oracle, "get_price", asset)
        except Exception:
            raise ContractError.ORACLE_READ_FAILED
