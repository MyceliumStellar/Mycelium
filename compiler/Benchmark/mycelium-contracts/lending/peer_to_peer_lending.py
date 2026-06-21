"""
Peer-To-Peer (P2P) Lending — Direct matched lending with escrowed collateral and amortization.

Mycelium Smart Contract for Stellar
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


# ── Error Codes ──────────────────────────────────────────────────────────────

class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    REQUEST_NOT_FOUND = 4
    OFFER_NOT_FOUND = 5
    LOAN_NOT_FOUND = 6
    INVALID_STATUS = 7
    INSUFFICIENT_COLLATERAL = 8
    COLLATERAL_MISMATCH = 9
    INTEREST_RATE_TOO_LOW = 10
    DURATION_TOO_LONG = 11
    GRACE_PERIOD_ACTIVE = 12
    LOAN_NOT_DEFAULTED = 13
    ZERO_AMOUNT = 14
    OVERFLOW = 15
    PAST_DUE = 16


# ── Constants ────────────────────────────────────────────────────────────────

SECONDS_PER_YEAR = U128(31_536_000)
BPS_DENOMINATOR = U128(10000)
DEFAULT_GRACE_PERIOD = U64(259_200)  # 3 days in seconds


@contract
class PeerToPeerLending:
    """
    Direct matching P2P lending contract.
    Borrowers can submit Loan Requests locking collateral in escrow.
    Lenders can fund these requests directly, starting an amortization schedule.
    Lenders can also create Lending Offers, which borrowers can fill by providing collateral.
    If a borrower misses a payment beyond the grace period, the lender can default the loan
    and seize the escrowed collateral.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address):
        """
        Initializes the contract. Sets the administrator.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("request_count", U64(0))
        self.storage.set("offer_count", U64(0))
        self.storage.set("loan_count", U64(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin})

    # ── Request / Borrower Operations ────────────────────────────────────────

    @external
    def create_request(
        self,
        borrower: Address,
        asset: Address,
        amount: U128,
        interest_rate_bps: U128,
        collateral_token: Address,
        collateral_amount: U128,
        duration_seconds: U64,
        installments_count: U64,
    ) -> U64:
        """
        Creates a loan request and locks the required collateral in the escrow contract.
        """
        borrower.require_auth()
        self._require_initialized()

        if amount == U128(0) or collateral_amount == U128(0) or installments_count == U64(0):
            raise ContractError.ZERO_AMOUNT

        # Transfer collateral to escrow
        self.env.transfer(borrower, self.env.current_contract(), collateral_token, collateral_amount)

        request_id = self.storage.get("request_count", U64(0))

        request = {
            "borrower": borrower,
            "asset": asset,
            "amount": amount,
            "interest_rate": interest_rate_bps,
            "collateral_token": collateral_token,
            "collateral_amount": collateral_amount,
            "duration": duration_seconds,
            "installments": installments_count,
            "active": True,
        }
        self.storage.set(f"request:{request_id}", request)
        self.storage.set("request_count", request_id + U64(1))

        self.env.emit_event("request_created", {
            "request_id": request_id,
            "borrower": borrower,
            "asset": asset,
            "amount": amount,
            "collateral_amount": collateral_amount,
        })
        return request_id

    @external
    def cancel_request(self, caller: Address, request_id: U64):
        """
        Cancels a pending request and returns the collateral to the borrower.
        """
        caller.require_auth()
        self._require_initialized()

        request = self._get_request(request_id)
        if request["borrower"] != caller:
            raise ContractError.UNAUTHORIZED
        if not request["active"]:
            raise ContractError.INVALID_STATUS

        request["active"] = False
        self.storage.set(f"request:{request_id}", request)

        # Refund collateral
        self.env.transfer(self.env.current_contract(), caller, request["collateral_token"], request["collateral_amount"])

        self.env.emit_event("request_cancelled", {"request_id": request_id})

    # ── Match Request (Lender funds request) ─────────────────────────────────

    @external
    def fund_request(self, lender: Address, request_id: U64) -> U64:
        """
        Lender funds a borrower's request. Transfers principal and starts the loan.
        """
        lender.require_auth()
        self._require_initialized()

        request = self._get_request(request_id)
        if not request["active"]:
            raise ContractError.INVALID_STATUS

        request["active"] = False
        self.storage.set(f"request:{request_id}", request)

        # Transfer principal from lender to borrower
        self.env.transfer(lender, request["borrower"], request["asset"], request["amount"])

        # Calculate amortization schedule
        # Simple interest total = principal * rate * duration / (seconds_per_year * 10000)
        principal = request["amount"]
        rate = request["interest_rate"]
        duration = U128(request["duration"])
        total_interest = (principal * rate * duration) // (SECONDS_PER_YEAR * BPS_DENOMINATOR)
        total_repayment = principal + total_interest
        
        installments = request["installments"]
        installment_amount = total_repayment // U128(installments)

        loan_id = self.storage.get("loan_count", U64(0))
        now = self.env.ledger().timestamp()

        loan = {
            "lender": lender,
            "borrower": request["borrower"],
            "asset": request["asset"],
            "principal": principal,
            "interest_rate": rate,
            "collateral_token": request["collateral_token"],
            "collateral_amount": request["collateral_amount"],
            "start_time": now,
            "duration": request["duration"],
            "installments": installments,
            "installments_paid": U64(0),
            "amount_paid": U128(0),
            "next_due_time": now + request["duration"] // installments,
            "installment_amount": installment_amount,
            "grace_period": DEFAULT_GRACE_PERIOD,
            "status": U64(1),  # 1 = Active, 2 = Repaid, 3 = Defaulted
        }
        self.storage.set(f"loan:{loan_id}", loan)
        self.storage.set("loan_count", loan_id + U64(1))

        self.env.emit_event("loan_started", {
            "loan_id": loan_id,
            "lender": lender,
            "borrower": request["borrower"],
            "principal": principal,
            "repayment_total": total_repayment,
        })
        return loan_id

    # ── Repayment / Amortization ─────────────────────────────────────────────

    @external
    def pay_installment(self, caller: Address, loan_id: U64):
        """
        Borrower pays the next due installment amount. Transferred to lender.
         Releases collateral on final repayment.
        """
        caller.require_auth()
        self._require_initialized()

        loan = self._get_loan(loan_id)
        if loan["status"] != U64(1):  # Must be Active
            raise ContractError.INVALID_STATUS

        installment_amount = loan["installment_amount"]

        # Transfer installment payment from borrower (caller) to lender
        self.env.transfer(caller, loan["lender"], loan["asset"], installment_amount)

        # Update loan schedule
        loan["installments_paid"] += U64(1)
        loan["amount_paid"] += installment_amount

        # Check if fully repaid
        if loan["installments_paid"] == loan["installments"]:
            loan["status"] = U64(2)  # Repaid
            self.storage.set(f"loan:{loan_id}", loan)

            # Return escrowed collateral to borrower
            self.env.transfer(self.env.current_contract(), loan["borrower"], loan["collateral_token"], loan["collateral_amount"])

            self.env.emit_event("loan_repaid", {
                "loan_id": loan_id,
                "borrower": loan["borrower"],
            })
        else:
            # Advance due date for next installment
            loan["next_due_time"] = loan["start_time"] + (loan["installments_paid"] + U64(1)) * (loan["duration"] // loan["installments"])
            self.storage.set(f"loan:{loan_id}", loan)

            self.env.emit_event("installment_paid", {
                "loan_id": loan_id,
                "installment_index": loan["installments_paid"],
                "next_due_time": loan["next_due_time"],
            })

    # ── Default Claim ────────────────────────────────────────────────────────

    @external
    def claim_default(self, caller: Address, loan_id: U64):
        """
        Declared by lender if borrower missed payment deadline + grace period.
        Seizes escrowed collateral and transfers to lender.
        """
        caller.require_auth()
        self._require_initialized()

        loan = self._get_loan(loan_id)
        if loan["lender"] != caller:
            raise ContractError.UNAUTHORIZED
        if loan["status"] != U64(1):
            raise ContractError.INVALID_STATUS

        now = self.env.ledger().timestamp()
        due_limit = loan["next_due_time"] + loan["grace_period"]

        if now <= due_limit:
            raise ContractError.GRACE_PERIOD_ACTIVE

        # Mark loan as defaulted
        loan["status"] = U64(3)  # Defaulted
        self.storage.set(f"loan:{loan_id}", loan)

        # Transfer collateral to lender
        self.env.transfer(self.env.current_contract(), loan["lender"], loan["collateral_token"], loan["collateral_amount"])

        self.env.emit_event("loan_defaulted", {
            "loan_id": loan_id,
            "lender": loan["lender"],
            "collateral_seized": loan["collateral_amount"],
        })

    # ── View Functions ───────────────────────────────────────────────────────

    @view
    def get_request_info(self, request_id: U64) -> Map:
        """Returns details of a loan request."""
        return self._get_request(request_id)

    @view
    def get_loan_info(self, loan_id: U64) -> Map:
        """Returns details and schedule status of an active loan."""
        return self._get_loan(loan_id)

    @view
    def get_loan_counts(self) -> Map:
        """Returns cumulative request, offer, and active loan counts."""
        return {
            "request_count": self.storage.get("request_count", U64(0)),
            "loan_count": self.storage.get("loan_count", U64(0)),
        }

    # ── Internal Helpers ─────────────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _get_request(self, request_id: U64) -> Map:
        request = self.storage.get(f"request:{request_id}", None)
        if request is None:
            raise ContractError.REQUEST_NOT_FOUND
        return request

    def _get_loan(self, loan_id: U64) -> Map:
        loan = self.storage.get(f"loan:{loan_id}", None)
        if loan is None:
            raise ContractError.LOAN_NOT_FOUND
        return loan
