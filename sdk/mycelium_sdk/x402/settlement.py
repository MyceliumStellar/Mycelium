"""
x402 — machine-to-machine commerce primitives (conditional escrow + settlement).

Both halves are real on-chain operations routed through `AgentContext`:
  - `create_locked_escrow` deploys an instance of the bundled escrow contract
    (`mycelium_sdk/contracts/escrow.wasm`, compiled from `escrow_contract.py`)
    and locks the payment by invoking `initialize`, naming a `judge` as the
    release authority.
  - `release_funds` / `split_release` invoke `claim_funds` / `claim_and_split`
    on an existing escrow. These must be signed by the escrow's `judge` (the
    verdict authority), not the depositor — release follows a judge's verdict on
    the worker's evidence, not a SHA-256 preimage. `evidence_root` ties the
    payout to the approved submission and is recorded on-chain for audit.

No mocks: deployment uses the same pure-Python signed-transaction flow as
`mycelium deploy` (`AgentContext.deploy_contract`), and the lock/claim are
signed Soroban transactions.
"""

import os
from decimal import Decimal

from mycelium_sdk.scval import u64

# 1 XLM = 10,000,000 stroops; Soroban token amounts are integer stroops (i128).
STROOPS_PER_XLM = 10_000_000
# Soroban token amounts are i128; reject anything that can't fit before we ever
# build a transaction (the escrow re-checks on-chain, but fail early + clearly).
I128_MAX = (1 << 127) - 1
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
        judge: str,
        token: str | None = None,
        timeout_seconds: int = DEFAULT_ESCROW_TIMEOUT_SECONDS,
    ) -> str:
        """
        Deploy an escrow instance and lock `amount_xlm` payable to `provider_id`,
        releasable when `judge` authorizes it on a passing verdict
        (`release_funds` / `split_release`). The depositor may refund after
        `timeout_seconds` if no release happens.

        Returns the deployed escrow contract address. `token` defaults to the
        network's native-XLM Stellar Asset Contract.
        """
        if not os.path.exists(_ESCROW_WASM):
            raise FileNotFoundError(
                f"Bundled escrow WASM missing at {_ESCROW_WASM}. Reinstall "
                "mycelium-sdk, or rebuild it with "
                "`mycelium compile contracts/escrow_contract.py -o "
                "mycelium_sdk/contracts/escrow.wasm`."
            )

        from mycelium_sdk.constants import native_token_address

        # Validate the amount BEFORE deploying anything (a bad amount otherwise
        # burns a deploy tx on an escrow that can never be funded correctly).
        amount = Decimal(str(amount_xlm))
        if amount <= 0:
            raise ValueError(f"Escrow amount must be positive (got {amount_xlm} XLM).")
        amount_stroops = int(amount * STROOPS_PER_XLM)
        if amount_stroops <= 0:
            raise ValueError(
                f"Escrow amount {amount_xlm} XLM rounds to 0 stroops; use at least "
                f"0.0000001 XLM (1 stroop)."
            )
        if amount_stroops > I128_MAX:
            raise ValueError(
                f"Escrow amount {amount_xlm} XLM exceeds the i128 token ceiling."
            )
        if timeout_seconds <= 0:
            raise ValueError(f"Escrow timeout must be positive (got {timeout_seconds}s).")

        if not judge:
            raise ValueError("create_locked_escrow requires a judge address (the release authority).")

        token = token or native_token_address(self.context.network_type)
        depositor = self.context.keypair.public_key

        print(
            f"[x402] Deploying escrow + locking {amount_xlm} XLM for "
            f"provider {provider_id} (judge {judge[:6]}…)..."
        )
        escrow_id = self._deploy_escrow_instance()

        # Lock the funds: initialize(depositor, provider, token, amount, judge, timeout).
        self.context.call_contract(
            contract_id=escrow_id,
            function_name="initialize",
            args=[depositor, provider_id, token, amount_stroops, judge, u64(timeout_seconds)],
        )
        print(f"[x402] Escrow live at {escrow_id} (funds locked).")
        return escrow_id

    def release_funds(self, escrow_contract_id: str, evidence_root: bytes):
        """
        Disburse locked funds by invoking `claim_funds(evidence_root)` on the
        escrow. Must be signed by the escrow's judge (the verdict authority);
        `evidence_root` ties the payout to the approved submission. Returns the
        TxResult.
        """
        print("[x402] Verdict passed. Releasing locked funds to provider...")
        return self.context.call_contract(
            contract_id=escrow_contract_id,
            function_name="claim_funds",
            args=[evidence_root],
        )

    def split_release(self, escrow_contract_id: str, shares, evidence_root: bytes):
        """
        Release locked escrow funds across N recipients (a swarm), splitting the
        locked amount by `shares`. `shares` is a list of `(recipient_address,
        share_bps)` whose basis points must sum to 10000. Invokes
        `claim_and_split(evidence_root, recipients, amounts)` on the escrow — must
        be signed by the escrow's judge. Returns the TxResult.

        The exact stroop amounts are computed here so they sum to the locked
        amount with no rounding dust (the remainder lands on the last recipient);
        the escrow re-checks that the amounts balance before paying out.
        """
        if not shares:
            raise ValueError("split_release requires at least one (recipient, share_bps).")
        for recipient, bps in shares:
            if not recipient:
                raise ValueError("split_release: every share needs a recipient address.")
            if int(bps) <= 0:
                raise ValueError(
                    f"Swarm share basis points must be positive (got {bps} for {recipient})."
                )
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
            args=[evidence_root, recipients, amounts],
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
    """
    Deprecated alias for EscrowPaymentRouter. The escrow no longer releases on a
    SHA-256 preimage — it releases on a `judge`'s verdict — so a `judge` address
    is now required where a `task_id` once stood.
    """

    def create_escrow_payment(self, recipient_id: str, amount_xlm: float, judge: str) -> str:
        return self.create_locked_escrow(recipient_id, Decimal(str(amount_xlm)), judge)

    def disburse_payment(self, escrow_id: str, evidence_root) -> bool:
        root = evidence_root.encode("utf-8") if isinstance(evidence_root, str) else evidence_root
        self.release_funds(escrow_id, root)
        return True
