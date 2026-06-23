"""
x402 — machine-to-machine commerce primitives (conditional escrow + settlement).

Both halves are real on-chain operations routed through `AgentContext`:
  - `create_locked_escrow` deploys an instance of the bundled escrow contract
    (`mycelium_sdk/contracts/escrow.wasm`, compiled from `escrow_contract.py`)
    and locks the payment by invoking `initialize`.
  - `release_funds` invokes `claim_funds(proof)` on an existing escrow.

No mocks: deployment uses the same pure-Python signed-transaction flow as
`mycelium deploy` (`AgentContext.deploy_contract`), and the lock/claim are
signed Soroban transactions.
"""

import os
from decimal import Decimal

# 1 XLM = 10,000,000 stroops; Soroban token amounts are integer stroops (i128).
STROOPS_PER_XLM = 10_000_000
# Default escrow timeout (seconds) after which the depositor may refund.
DEFAULT_ESCROW_TIMEOUT_SECONDS = 24 * 60 * 60

# escrow.wasm is bundled at mycelium_sdk/contracts/ (one level up from this x402 subpackage).
_ESCROW_WASM = os.path.join(os.path.dirname(os.path.dirname(__file__)), "contracts", "escrow.wasm")


class EscrowPaymentRouter:
    def __init__(self, context):
        self.context = context

    def create_locked_escrow(
        self,
        provider_id: str,
        amount_xlm: Decimal,
        task_hash: bytes,
        token: str | None = None,
        timeout_seconds: int = DEFAULT_ESCROW_TIMEOUT_SECONDS,
    ) -> str:
        """
        Deploy an escrow instance and lock `amount_xlm` payable to `provider_id`,
        releasable once a proof of `task_hash` is published (`release_funds`).

        Returns the deployed escrow contract address. `token` defaults to the
        network's native-XLM Stellar Asset Contract.
        """
        if not os.path.exists(_ESCROW_WASM):
            raise FileNotFoundError(
                f"Bundled escrow WASM missing at {_ESCROW_WASM}. Reinstall "
                "mycelium-sdk, or rebuild it with "
                "`mycelium compile escrow_contract.py -o "
                "mycelium_sdk/contracts/escrow.wasm`."
            )

        from mycelium_sdk.constants import native_token_address

        token = token or native_token_address(self.context.network_type)
        amount_stroops = int(Decimal(str(amount_xlm)) * STROOPS_PER_XLM)
        depositor = self.context.keypair.public_key

        print(
            f"[x402] Deploying escrow + locking {amount_xlm} XLM for "
            f"provider {provider_id}..."
        )
        escrow_id = self._deploy_escrow_instance()

        # Lock the funds: initialize(depositor, provider, token, amount, hash, timeout).
        self.context.call_contract(
            contract_id=escrow_id,
            function_name="initialize",
            args=[depositor, provider_id, token, amount_stroops, task_hash, timeout_seconds],
        )
        print(f"[x402] Escrow live at {escrow_id} (funds locked).")
        return escrow_id

    def release_funds(self, escrow_contract_id: str, verification_proof: bytes):
        """
        Disburse locked funds by invoking `claim_funds(proof)` on the escrow
        contract. The proof must SHA-256 to the task hash. Returns the TxResult.
        """
        print("[x402] Confirming task execution. Triggering disbursement of funds...")
        return self.context.call_contract(
            contract_id=escrow_contract_id,
            function_name="claim_funds",
            args=[verification_proof],
        )

    def split_release(self, escrow_contract_id: str, shares, verification_proof: bytes):
        """
        Release locked escrow funds across N recipients (a swarm), splitting the
        locked amount by `shares`. `shares` is a list of `(recipient_address,
        share_bps)` whose basis points must sum to 10000. Invokes
        `claim_and_split(proof, recipients, amounts)` on the escrow — the `proof`
        must SHA-256 to the task hash. Returns the TxResult.

        The exact stroop amounts are computed here so they sum to the locked
        amount with no rounding dust (the remainder lands on the last recipient);
        the escrow re-checks that the amounts balance before paying out.
        """
        if not shares:
            raise ValueError("split_release requires at least one (recipient, share_bps).")
        total_bps = sum(int(bps) for _, bps in shares)
        if total_bps != 10000:
            raise ValueError(
                f"Swarm shares must sum to 10000 basis points (got {total_bps})."
            )

        # Read the locked amount from the escrow so the split is exact.
        details = self.context.call_contract(
            contract_id=escrow_contract_id,
            function_name="get_details",
            args=[],
            read_only=True,
        )
        amount = int(details["amount"] if isinstance(details, dict) else details[1])

        recipients = []
        amounts = []
        running = 0
        for i, (recipient, bps) in enumerate(shares):
            recipients.append(recipient)
            if i < len(shares) - 1:
                pay = amount * int(bps) // 10000
                running += pay
            else:
                pay = amount - running  # remainder absorbs rounding dust
            amounts.append(pay)

        print(
            f"[x402] Splitting {amount} stroops across {len(recipients)} recipients "
            f"({total_bps} bps)..."
        )
        return self.context.call_contract(
            contract_id=escrow_contract_id,
            function_name="claim_and_split",
            args=[verification_proof, recipients, amounts],
        )

    def refund(self, escrow_contract_id: str):
        """Reclaim locked funds after the escrow deadline. Returns the TxResult."""
        print("[x402] Requesting refund of expired escrow...")
        return self.context.call_contract(
            contract_id=escrow_contract_id,
            function_name="refund",
            args=[],
        )

    # ── deployment ───────────────────────────────────────────────────────────
    def _deploy_escrow_instance(self) -> str:
        """
        Upload + instantiate the bundled escrow WASM, returning its contract id.
        Pure-Python via `AgentContext.deploy_contract` (no stellar-cli / Rust).
        """
        with open(_ESCROW_WASM, "rb") as f:
            escrow_wasm_bytes = f.read()
        return self.context.deploy_contract(escrow_wasm_bytes)


# ── Back-compat aliases (previous class/method names) ────────────────────────
class EscrowPaymentManager(EscrowPaymentRouter):
    """Deprecated alias for EscrowPaymentRouter."""

    def create_escrow_payment(self, recipient_id: str, amount_xlm: float, task_id: str) -> str:
        # The escrow locks against a SHA-256 task_hash and releases on a preimage
        # proof, so hash the task id here; disburse_payment passes the raw id back
        # as the proof (sha256(proof) == task_hash).
        import hashlib

        task_hash = hashlib.sha256(task_id.encode("utf-8")).digest()
        return self.create_locked_escrow(recipient_id, Decimal(str(amount_xlm)), task_hash)

    def disburse_payment(self, escrow_id: str, signature_proof) -> bool:
        proof = signature_proof.encode("utf-8") if isinstance(signature_proof, str) else signature_proof
        self.release_funds(escrow_id, proof)
        return True
