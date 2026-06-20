"""
Payroll System — Recurring employee salaries, tax withholdings, compliance reporting, and funding management.

Mycelium Smart Contract for Stellar. Tracks employee registries, gross salaries, pay cycles, and tax rates.
Manages an escrowed payroll pool in stablecoin. Processes paychecks periodically, calculating and routing
tax withholdings to the tax authority and net salaries to employees, logging compliance data for auditing.
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
    EMPLOYEE_NOT_FOUND = 5
    EMPLOYEE_ALREADY_EXISTS = 6
    INSUFFICIENT_POOL_BALANCE = 7
    PAYROLL_NOT_DUE = 8
    INVALID_PARAM = 9

@contract
class PayrollSystem:
    """
    Payroll System contract tracking recurring salaries, tax withholdings, and compliance.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        tax_collector: Address,
        stablecoin: Address
    ):
        """Initialize configurations, tax authority and funding token."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("tax_collector", tax_collector)
        self.storage.set("stablecoin", stablecoin)
        self.storage.set("pool_balance", U128(0))
        self.storage.set("payout_nonce", U64(1))
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "tax_collector": tax_collector,
            "stablecoin": stablecoin
        })

    @external
    def register_employee(
        self,
        caller: Address,
        employee: Address,
        gross_salary: U128,
        tax_rate_bps: U64,      # e.g. 2000 for 20% withholding tax
        pay_interval: U64,      # in seconds, e.g. 2592000 for monthly
        first_pay_date: U64
    ):
        """Register a new employee with payroll parameters (Admin/Employer only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        prefix = f"emp_{employee}_"
        if self.storage.get(prefix + "active", False):
            raise ContractError.EMPLOYEE_ALREADY_EXISTS

        if gross_salary == U128(0) or tax_rate_bps > U64(10000) or pay_interval == U64(0):
            raise ContractError.INVALID_PARAM

        self.storage.set(prefix + "active", True)
        self.storage.set(prefix + "gross_salary", gross_salary)
        self.storage.set(prefix + "tax_rate_bps", tax_rate_bps)
        self.storage.set(prefix + "pay_interval", pay_interval)
        self.storage.set(prefix + "last_pay_date", first_pay_date - pay_interval)
        self.storage.set(prefix + "next_pay_date", first_pay_date)

        self.env.emit_event("employee_registered", {
            "employee": employee,
            "gross_salary": gross_salary,
            "tax_rate_bps": tax_rate_bps,
            "first_pay_date": first_pay_date
        })

    @external
    def deposit_payroll_pool(self, caller: Address, amount: U128):
        """Employer deposits stablecoins to fund the payroll escrow pool."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if amount == U128(0):
            raise ContractError.INVALID_PARAM

        stablecoin = self.storage.get("stablecoin")
        contract_addr = self.env.current_contract_address()
        self.env.call(stablecoin, "transfer", caller, contract_addr, amount)

        pool = self.storage.get("pool_balance", U128(0))
        self.storage.set("pool_balance", pool + amount)

        self.env.emit_event("pool_funded", {
            "depositor": caller,
            "amount": amount,
            "total_pool": pool + amount
        })

    @external
    def process_payroll(self, caller: Address, employee: Address):
        """
        Process payroll paycheck for a specific employee if their interval has elapsed.
        Calculates and splits withholding tax and net pay, updating the employee's schedules.
        Can be called by employee, admin, or keeper.
        """
        self._require_initialized()
        self._require_not_paused()

        prefix = f"emp_{employee}_"
        if not self.storage.get(prefix + "active", False):
            raise ContractError.EMPLOYEE_NOT_FOUND

        now = self._get_now()
        next_pay = self.storage.get(prefix + "next_pay_date", U64(0))
        if now < next_pay:
            raise ContractError.PAYROLL_NOT_DUE

        gross = self.storage.get(prefix + "gross_salary", U128(0))
        tax_rate = self.storage.get(prefix + "tax_rate_bps", U64(0))
        interval = self.storage.get(prefix + "pay_interval", U64(0))

        # Withholding and Net pay calculation
        tax_withheld = (gross * U128(tax_rate)) / U128(10000)
        net_pay = gross - tax_withheld

        # Check pool liquidity
        pool = self.storage.get("pool_balance", U128(0))
        if pool < gross:
            raise ContractError.INSUFFICIENT_POOL_BALANCE

        # Deduct pool balance
        self.storage.set("pool_balance", pool - gross)

        # Update pay dates
        self.storage.set(prefix + "last_pay_date", now)
        self.storage.set(prefix + "next_pay_date", next_pay + interval)

        # Distribute payments
        stablecoin = self.storage.get("stablecoin")
        contract_addr = self.env.current_contract_address()
        tax_collector = self.storage.get("tax_collector")

        # Pay Net Salary to Employee
        if net_pay > U128(0):
            self.env.call(stablecoin, "transfer", contract_addr, employee, net_pay)

        # Pay Withholding Tax to Collector
        if tax_withheld > U128(0):
            self.env.call(stablecoin, "transfer", contract_addr, tax_collector, tax_withheld)

        # Log compliance record in storage
        payout_id = self.storage.get("payout_nonce", U64(1))
        self.storage.set("payout_nonce", payout_id + U64(1))

        pay_prefix = f"payout_{payout_id}_"
        self.storage.set(pay_prefix + "employee", employee)
        self.storage.set(pay_prefix + "gross", gross)
        self.storage.set(pay_prefix + "net", net_pay)
        self.storage.set(pay_prefix + "tax", tax_withheld)
        self.storage.set(pay_prefix + "time", now)

        self.env.emit_event("payroll_processed", {
            "payout_id": payout_id,
            "employee": employee,
            "gross": gross,
            "net": net_pay,
            "tax": tax_withheld
        })

    @external
    def update_employee_terms(
        self,
        caller: Address,
        employee: Address,
        gross_salary: U128,
        tax_rate_bps: U64
    ):
        """Update employee salary and tax withholding terms (Admin/Employer only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        prefix = f"emp_{employee}_"
        if not self.storage.get(prefix + "active", False):
            raise ContractError.EMPLOYEE_NOT_FOUND

        if gross_salary == U128(0) or tax_rate_bps > U64(10000):
            raise ContractError.INVALID_PARAM

        self.storage.set(prefix + "gross_salary", gross_salary)
        self.storage.set(prefix + "tax_rate_bps", tax_rate_bps)

        self.env.emit_event("employee_terms_updated", {
            "employee": employee,
            "gross_salary": gross_salary,
            "tax_rate_bps": tax_rate_bps
        })

    @external
    def deactivate_employee(self, caller: Address, employee: Address, settle_prorated: Bool):
        """
        Deactivate employee from payroll.
        If settle_prorated is True, calculates and distributes prorated salary for outstanding days.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        prefix = f"emp_{employee}_"
        if not self.storage.get(prefix + "active", False):
            raise ContractError.EMPLOYEE_NOT_FOUND

        if settle_prorated:
            now = self._get_now()
            last_pay = self.storage.get(prefix + "last_pay_date", U64(0))
            interval = self.storage.get(prefix + "pay_interval", U64(1))
            gross = self.storage.get(prefix + "gross_salary", U128(0))
            tax_rate = self.storage.get(prefix + "tax_rate_bps", U64(0))

            if now > last_pay:
                elapsed = now - last_pay
                if elapsed < interval:
                    # Prorated calculation: gross * elapsed / interval
                    prorated_gross = (gross * U128(elapsed)) / U128(interval)
                    tax_withheld = (prorated_gross * U128(tax_rate)) / U128(10000)
                    net_pay = prorated_gross - tax_withheld

                    # Transfer if pool has funds
                    pool = self.storage.get("pool_balance", U128(0))
                    if pool >= prorated_gross:
                        self.storage.set("pool_balance", pool - prorated_gross)
                        stablecoin = self.storage.get("stablecoin")
                        contract_addr = self.env.current_contract_address()
                        tax_collector = self.storage.get("tax_collector")

                        if net_pay > U128(0):
                            self.env.call(stablecoin, "transfer", contract_addr, employee, net_pay)
                        if tax_withheld > U128(0):
                            self.env.call(stablecoin, "transfer", contract_addr, tax_collector, tax_withheld)
                        
                        self.env.emit_event("prorated_settled", {
                            "employee": employee,
                            "gross": prorated_gross,
                            "net": net_pay
                        })

        # Deactivate
        self.storage.set(prefix + "active", False)
        self.env.emit_event("employee_deactivated", {
            "employee": employee
        })

    @external
    def withdraw_pool_funds(self, caller: Address, amount: U128):
        """Employer withdraws unused stablecoins from pool (Admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        pool = self.storage.get("pool_balance", U128(0))
        if amount > pool:
            raise ContractError.INVALID_PARAM

        self.storage.set("pool_balance", pool - amount)

        stablecoin = self.storage.get("stablecoin")
        self.env.call(stablecoin, "transfer", self.env.current_contract_address(), caller, amount)

        self.env.emit_event("pool_withdrawn", {
            "recipient": caller,
            "amount": amount,
            "remaining": pool - amount
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause/unpause payroll distributions (admin only)."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    # --- VIEWS ---

    @view
    def get_employee(self, employee: Address) -> Map:
        """Query employee details."""
        res = Map(self.env)
        prefix = f"emp_{employee}_"
        active = self.storage.get(prefix + "active", False)
        res.set("active", active)
        if active:
            res.set("gross_salary", self.storage.get(prefix + "gross_salary"))
            res.set("tax_rate_bps", self.storage.get(prefix + "tax_rate_bps"))
            res.set("pay_interval", self.storage.get(prefix + "pay_interval"))
            res.set("last_pay_date", self.storage.get(prefix + "last_pay_date"))
            res.set("next_pay_date", self.storage.get(prefix + "next_pay_date"))
        return res

    @view
    def get_pool_balance(self) -> U128:
        """Query funding pool balance."""
        return self.storage.get("pool_balance", U128(0))

    @view
    def get_compliance_payout(self, payout_id: U64) -> Map:
        """Query historical payout for auditing/compliance."""
        res = Map(self.env)
        pay_prefix = f"payout_{payout_id}_"
        emp = self.storage.get(pay_prefix + "employee")
        if emp is not None:
            res.set("employee", emp)
            res.set("gross", self.storage.get(pay_prefix + "gross"))
            res.set("net", self.storage.get(pay_prefix + "net"))
            res.set("tax", self.storage.get(pay_prefix + "tax"))
            res.set("time", self.storage.get(pay_prefix + "time"))
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
