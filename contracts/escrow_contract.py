"""
Escrow — the x402 conditional-payment contract for Mycelium agents.

One escrow instance locks a payment from a depositor to a provider (or a swarm)
until a **verdict** authorizes release. The release authority is a `judge`
address fixed at lock time: the judge evaluates the worker's deliverable against
the job's rubric off-chain (see `PROOF_SYSTEM.md`) and, on a pass, authorizes the
payout on-chain. If the deadline passes without a release, the depositor refunds.

This replaces the previous SHA-256 preimage gate. A hash preimage only proved the
claimant could echo the agreed bytes back — it never proved the work was done or
was any good. Release now follows a judge's verdict, not a tautological hash.

`evidence_root` (the 32-byte commitment to the worker's submitted evidence
bundle) is passed on release and emitted, so every payout is auditably tied to
the exact submission the judge approved. The contract does not re-derive it — the
binding is the judge's authorization plus the on-chain record.

Funds move via the Soroban token interface (`env.transfer`), so the locked asset
can be native XLM (its Stellar Asset Contract) or any SEP-41 token.

Authored in the Mycelium DSL and compiled with this repo's own compiler:

    python -m mycelium_compiler.main escrow_contract.py -o build/escrow.wasm

The compiled WASM is bundled into `mycelium_sdk` and instantiated on demand by
`mycelium_sdk.x402.EscrowPaymentRouter.create_locked_escrow`.
"""

from mycelium import (
    contract, external, view,
    Address, U64, I128, Bytes, Bool, Map, Vec, Env, Symbol,
)


class ContractError:
    ALREADY_INITIALIZED = 1
    NOT_INITIALIZED = 2
    ALREADY_SETTLED = 3
    INVALID_PROOF = 4
    NOT_EXPIRED = 5
    BAD_SPLIT = 6
    FEE_TOO_HIGH = 7


# Protocol fee ceiling (basis points). A depositor authorizes `initialize`, so
# they consent to the fee — but the contract still hard-caps it so a buggy or
# malicious caller can never skim more than 10% of a worker's bounty.
MAX_FEE_BPS = 1000


@contract
class Escrow:
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        depositor: Address,
        provider: Address,
        token: Address,
        amount: I128,
        judge: Address,
        timeout: U64,
        fee_bps: I128,
        fee_collector: Address,
    ) -> Bool:
        """
        Lock `amount` of `token` from `depositor`, payable to `provider` (or a
        swarm via `claim_and_split`) once `judge` authorizes release on a passing
        verdict. `timeout` seconds after creation the depositor may refund
        instead. Reverts if already initialized.

        `fee_bps` is the protocol take-rate in basis points (e.g. 250 = 2.5%),
        skimmed to `fee_collector` on release only — refunds pay no fee. Set
        `fee_bps = 0` to disable the fee (testnet / fee-free deployments). The
        fee is capped at `MAX_FEE_BPS` so a worker's payout can never be gutted.
        """
        depositor.require_auth()
        if self.storage.get("init", False):
            raise ContractError.ALREADY_INITIALIZED

        if fee_bps < I128(0):
            raise ContractError.FEE_TOO_HIGH
        if fee_bps > I128(MAX_FEE_BPS):
            raise ContractError.FEE_TOO_HIGH

        # Pull the locked funds from the depositor into this contract.
        self.env.transfer(depositor, self.env.current_contract_address(), token, amount)

        self.storage.set("depositor", depositor)
        self.storage.set("provider", provider)
        self.storage.set("token", token)
        self.storage.set("amount", amount)
        self.storage.set("judge", judge)
        self.storage.set("deadline", self.env.ledger().timestamp() + timeout)
        self.storage.set("fee_bps", fee_bps)
        self.storage.set("fee_collector", fee_collector)
        self.storage.set("settled", False)
        self.storage.set("init", True)

        self.env.emit_event("escrow_locked", {"provider": provider, "amount": amount, "judge": judge})
        return True

    @external
    def claim_funds(self, evidence_root: Bytes) -> Bool:
        """
        Release the locked funds to the provider. The `judge` recorded at lock
        time must authorize the release (a passing verdict on the worker's
        submission, evaluated off-chain). `evidence_root` ties the payout to the
        approved evidence bundle and is emitted for audit. Reverts if
        uninitialized, already settled, or unauthorized.
        """
        if not self.storage.get("init", False):
            raise ContractError.NOT_INITIALIZED
        if self.storage.get("settled", False):
            raise ContractError.ALREADY_SETTLED

        judge = self.storage.get("judge")
        judge.require_auth()

        provider = self.storage.get("provider")
        token = self.storage.get("token")
        amount = self.storage.get("amount")

        # Skim the protocol fee off the top; the provider is paid the remainder.
        fee_bps = self.storage.get("fee_bps", I128(0))
        fee = amount * fee_bps / I128(10000)
        net = amount - fee

        contract_addr = self.env.current_contract_address()
        self.env.transfer(contract_addr, provider, token, net)
        if fee > I128(0):
            fee_collector = self.storage.get("fee_collector")
            self.env.transfer(contract_addr, fee_collector, token, fee)

        self.storage.set("settled", True)
        self.storage.set("evidence_root", evidence_root)
        self.env.emit_event("escrow_released", {"provider": provider, "amount": net, "fee": fee})
        return True

    @external
    def claim_and_split(
        self,
        evidence_root: Bytes,
        recipients: Vec[Address],
        amounts: Vec[I128],
    ) -> Bool:
        """
        Release the locked funds across N recipients (a swarm), paying
        `amounts[i]` of the locked token to `recipients[i]`. The `judge` recorded
        at lock time must authorize the release; the two vectors must be the same
        length and the amounts must sum to the locked amount. Powers the Sovereign
        Job Boards multi-agent bounty split (`EscrowPaymentRouter.split_release`).

        Judge authorization is what prevents an observer from redirecting the
        bounty: only the verdict authority can name the recipients. `evidence_root`
        ties the payout to the approved submission. Reverts if uninitialized,
        already settled, unauthorized, or the split does not balance.
        """
        if not self.storage.get("init", False):
            raise ContractError.NOT_INITIALIZED
        if self.storage.get("settled", False):
            raise ContractError.ALREADY_SETTLED

        judge = self.storage.get("judge")
        judge.require_auth()

        n = len(recipients)
        if n != len(amounts):
            raise ContractError.BAD_SPLIT

        token = self.storage.get("token")
        amount = self.storage.get("amount")

        # The swarm splits the NET bounty (locked amount minus the protocol fee);
        # the caller-supplied amounts must therefore sum to `net`, not `amount`.
        fee_bps = self.storage.get("fee_bps", I128(0))
        fee = amount * fee_bps / I128(10000)
        net = amount - fee

        total = I128(0)
        for i in range(n):
            total = total + amounts[i]
        if total != net:
            raise ContractError.BAD_SPLIT

        contract_addr = self.env.current_contract_address()
        for i in range(n):
            self.env.transfer(contract_addr, recipients[i], token, amounts[i])
        if fee > I128(0):
            fee_collector = self.storage.get("fee_collector")
            self.env.transfer(contract_addr, fee_collector, token, fee)

        self.storage.set("settled", True)
        self.storage.set("evidence_root", evidence_root)
        self.env.emit_event("escrow_split", {"recipients": n, "amount": net, "fee": fee})
        return True

    @external
    def refund(self) -> Bool:
        """
        Return the locked funds to the depositor after the deadline. Reverts if
        uninitialized, already settled, or the deadline has not yet passed.
        """
        if not self.storage.get("init", False):
            raise ContractError.NOT_INITIALIZED
        if self.storage.get("settled", False):
            raise ContractError.ALREADY_SETTLED

        depositor = self.storage.get("depositor")
        depositor.require_auth()
        if self.env.ledger().timestamp() < self.storage.get("deadline", U64(0)):
            raise ContractError.NOT_EXPIRED

        token = self.storage.get("token")
        amount = self.storage.get("amount")
        self.env.transfer(self.env.current_contract_address(), depositor, token, amount)

        self.storage.set("settled", True)
        self.env.emit_event("escrow_refunded", {"depositor": depositor, "amount": amount})
        return True

    @view
    def get_details(self) -> Map:
        """Return the escrow's current state for off-chain inspection."""
        details = Map()
        details.set(Symbol("provider"), self.storage.get("provider"))
        details.set(Symbol("amount"), self.storage.get("amount"))
        details.set(Symbol("judge"), self.storage.get("judge"))
        details.set(Symbol("deadline"), self.storage.get("deadline"))
        details.set(Symbol("fee_bps"), self.storage.get("fee_bps", I128(0)))
        details.set(Symbol("settled"), self.storage.get("settled", False))
        return details
