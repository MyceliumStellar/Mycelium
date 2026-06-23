"""
Escrow — the x402 conditional-payment contract for Mycelium agents.

One escrow contract instance locks a payment from a depositor to a provider
until the provider publishes a proof whose SHA-256 matches the agreed
`task_hash`. If the deadline passes without a valid claim, the depositor can
refund. Funds move via the Soroban token interface (`env.transfer`), so the
locked asset can be native XLM (its Stellar Asset Contract) or any SEP-41 token.

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
        task_hash: Bytes,
        timeout: U64,
    ) -> Bool:
        """
        Lock `amount` of `token` from `depositor`, payable to `provider` once a
        proof of `task_hash` is published. `timeout` seconds after creation the
        depositor may refund instead. Reverts if already initialized.
        """
        depositor.require_auth()
        if self.storage.get("init", False):
            raise ContractError.ALREADY_INITIALIZED

        # Pull the locked funds from the depositor into this contract.
        self.env.transfer(depositor, self.env.current_contract_address(), token, amount)

        self.storage.set("depositor", depositor)
        self.storage.set("provider", provider)
        self.storage.set("token", token)
        self.storage.set("amount", amount)
        self.storage.set("task_hash", task_hash)
        self.storage.set("deadline", self.env.ledger().timestamp() + timeout)
        self.storage.set("settled", False)
        self.storage.set("init", True)

        self.env.emit_event("escrow_locked", {"provider": provider, "amount": amount})
        return True

    @external
    def claim_funds(self, proof: Bytes) -> Bool:
        """
        Release the locked funds to the provider. `proof` must hash (SHA-256) to
        the agreed `task_hash`. Reverts if uninitialized, already settled, or the
        proof is invalid.
        """
        if not self.storage.get("init", False):
            raise ContractError.NOT_INITIALIZED
        if self.storage.get("settled", False):
            raise ContractError.ALREADY_SETTLED
        if self.env.crypto().sha256(proof) != self.storage.get("task_hash"):
            raise ContractError.INVALID_PROOF

        provider = self.storage.get("provider")
        token = self.storage.get("token")
        amount = self.storage.get("amount")
        self.env.transfer(self.env.current_contract_address(), provider, token, amount)

        self.storage.set("settled", True)
        self.env.emit_event("escrow_released", {"provider": provider, "amount": amount})
        return True

    @external
    def claim_and_split(
        self,
        proof: Bytes,
        recipients: Vec[Address],
        amounts: Vec[I128],
    ) -> Bool:
        """
        Release the locked funds across N recipients (a swarm), paying
        `amounts[i]` of the locked token to `recipients[i]`. `proof` must hash
        (SHA-256) to the agreed `task_hash`. The two vectors must be the same
        length and the amounts must sum to the locked amount. Powers the
        Sovereign Job Boards multi-agent bounty split (`EscrowPaymentRouter.
        split_release`). Reverts if uninitialized, already settled, the proof is
        invalid, or the split does not balance.
        """
        if not self.storage.get("init", False):
            raise ContractError.NOT_INITIALIZED
        if self.storage.get("settled", False):
            raise ContractError.ALREADY_SETTLED
        if self.env.crypto().sha256(proof) != self.storage.get("task_hash"):
            raise ContractError.INVALID_PROOF

        n = len(recipients)
        if n != len(amounts):
            raise ContractError.BAD_SPLIT

        token = self.storage.get("token")
        amount = self.storage.get("amount")

        total = I128(0)
        for i in range(n):
            total = total + amounts[i]
        if total != amount:
            raise ContractError.BAD_SPLIT

        contract_addr = self.env.current_contract_address()
        for i in range(n):
            self.env.transfer(contract_addr, recipients[i], token, amounts[i])

        self.storage.set("settled", True)
        self.env.emit_event("escrow_split", {"recipients": n, "amount": amount})
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
        details.set(Symbol("deadline"), self.storage.get("deadline"))
        details.set(Symbol("settled"), self.storage.get("settled", False))
        return details
