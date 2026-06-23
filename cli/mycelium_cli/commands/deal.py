"""
`mycelium deal …` — wire two agents into an agent-to-agent (A2A) commerce deal.

Where `mycelium pay` is an *unconditional* transfer, a deal is *conditional*
x402 commerce between two agents: the payer locks funds into a fresh escrow
payable to a provider (resolved by Hive Registry unique name or raw address),
and the provider only collects once it publishes a proof of the agreed task.
If the provider never delivers, the payer reclaims the funds after the timeout.

This is the CLI front door to what `a2a_demo.py` / `EscrowPaymentRouter` do by
hand — two sovereign agents transacting purely through on-chain state:

  open    payer locks `amount` XLM to a provider against a task hash → escrow id
  release provider (or payer) publishes the proof preimage → funds disburse
  refund  payer reclaims the locked funds after the deadline passes
  status  read the escrow's current state (amount, provider, deadline, settled)

The escrow contract enforces the proof (SHA-256(proof) == task_hash) and the
deadline on-chain, so neither side has to trust the other. The board/registry
default from `mycelium.toml`, mirroring `deploy` / `register` / `job`.

Commands: open, release, refund, status.
"""

import hashlib
import os
from decimal import Decimal
from typing import Optional

import typer

from mycelium_cli.config import get_value

DEFAULT_WALLET_PATH = os.path.join(".mycelium", "wallet.json")
PASSPHRASE_ENV_VAR = "MYCELIUM_DECRYPT_KEY"
# Default escrow timeout (seconds) after which the payer may refund. Mirrors
# mycelium_sdk.x402.settlement.DEFAULT_ESCROW_TIMEOUT_SECONDS (24h).
DEFAULT_TIMEOUT_SECONDS = 24 * 60 * 60

deal_app = typer.Typer(help="Conditional agent-to-agent (A2A) commerce via x402 escrow.")


def _resolve_passphrase(label: str = "Wallet passphrase") -> str:
    """MYCELIUM_DECRYPT_KEY if set, else prompt — matches the rest of the CLI."""
    env_value = os.environ.get(PASSPHRASE_ENV_VAR)
    if env_value:
        return env_value
    return typer.prompt(label, hide_input=True)


def _context(network: Optional[str], wallet: str, *, signing: bool):
    """Build an AgentContext. Read-only commands skip wallet + passphrase."""
    from mycelium_sdk import AgentContext

    network = network or get_value("onchain", "network", "testnet")
    if signing:
        if not os.path.exists(wallet):
            typer.echo(f"Error: wallet {wallet} not found. Run `mycelium newwallet` first.")
            raise typer.Exit(code=1)
        return AgentContext(keypair_path=wallet, network_type=network, passphrase=_resolve_passphrase())
    return AgentContext.read_only(network_type=network)


def _resolve_agent_address(context, agent: str, registry: Optional[str]) -> str:
    """Pass through a G/C address; otherwise resolve a Hive Registry unique name."""
    from stellar_sdk import StrKey

    if StrKey.is_valid_ed25519_public_key(agent) or StrKey.is_valid_contract(agent):
        return agent
    from mycelium_sdk import HiveClient

    entry = HiveClient(context, registry_address=registry).resolve_agent(agent)
    addr = entry.get("public_key")
    if not addr:
        typer.echo(f"Error: could not resolve agent '{agent}' to an address.")
        raise typer.Exit(code=1)
    return addr


def _task_bytes(task: str) -> bytes:
    """A task file's raw bytes, or the UTF-8 bytes of a literal string."""
    if os.path.isfile(task):
        with open(task, "rb") as f:
            return f.read()
    return task.encode("utf-8")


@deal_app.command("open")
def open_deal(
    to: str = typer.Option(..., "--to", help="Provider: Hive Registry unique name or G/C address to pay"),
    amount: str = typer.Option(..., "--amount", help="Amount in XLM to lock for the provider"),
    task: str = typer.Option(..., "--task", help="Task spec file or string; its SHA-256 is the release condition"),
    token: str = typer.Option(None, "--token", help="Payment token contract (defaults to native XLM SAC)"),
    timeout: int = typer.Option(DEFAULT_TIMEOUT_SECONDS, "--timeout", help="Refund deadline in seconds"),
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
    registry: str = typer.Option(None, "--registry", help="Hive Registry id override (for name resolution)"),
):
    """Payer locks `amount` XLM to a provider against a task hash; prints the escrow id."""
    from mycelium_sdk.x402.settlement import EscrowPaymentRouter

    context = _context(network, wallet, signing=True)
    provider = _resolve_agent_address(context, to, registry)
    task_hash = hashlib.sha256(_task_bytes(task)).digest()

    typer.echo(f"[deal] Locking {amount} XLM to provider {to} ({provider[:8]}…) for {timeout}s...")
    try:
        escrow_id = EscrowPaymentRouter(context).create_locked_escrow(
            provider_id=provider,
            amount_xlm=Decimal(amount),
            task_hash=task_hash,
            token=token,
            timeout_seconds=timeout,
        )
    except Exception as e:  # noqa: BLE001
        typer.echo(f"❌ deal open failed: {e}")
        raise typer.Exit(code=1)
    typer.echo(f"✓ Deal opened. Escrow {escrow_id}")
    typer.echo(f"  Provider releases with: mycelium deal release {escrow_id} --proof <task>")
    typer.echo(f"  Payer refunds after {timeout}s with: mycelium deal refund {escrow_id}")


@deal_app.command("release")
def release(
    escrow_id: str = typer.Argument(..., help="Escrow contract id from `deal open`"),
    proof: str = typer.Option(..., "--proof", help="Proof file or string (must SHA-256 to the task hash)"),
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
):
    """Disburse the locked funds to the provider by publishing the task proof."""
    from mycelium_sdk.x402.settlement import EscrowPaymentRouter

    context = _context(network, wallet, signing=True)
    try:
        EscrowPaymentRouter(context).release_funds(escrow_id, _task_bytes(proof))
    except Exception as e:  # noqa: BLE001
        typer.echo(f"❌ deal release failed: {e}")
        raise typer.Exit(code=1)
    typer.echo(f"✓ Released escrow {escrow_id} — funds disbursed to the provider.")


@deal_app.command("refund")
def refund(
    escrow_id: str = typer.Argument(..., help="Escrow contract id from `deal open`"),
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
):
    """Payer reclaims the locked funds after the deadline passes."""
    from mycelium_sdk.x402.settlement import EscrowPaymentRouter

    context = _context(network, wallet, signing=True)
    try:
        EscrowPaymentRouter(context).refund(escrow_id)
    except Exception as e:  # noqa: BLE001
        typer.echo(f"❌ deal refund failed: {e}")
        raise typer.Exit(code=1)
    typer.echo(f"✓ Refunded escrow {escrow_id} — funds returned to the payer.")


@deal_app.command("status")
def status(
    escrow_id: str = typer.Argument(..., help="Escrow contract id from `deal open`"),
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
):
    """Show an escrow deal's current state (read-only, no wallet)."""
    context = _context(network, DEFAULT_WALLET_PATH, signing=False)
    try:
        details = context.call_contract(escrow_id, "get_details", [], read_only=True)
    except Exception as e:  # noqa: BLE001
        typer.echo(f"❌ deal status failed: {e}")
        raise typer.Exit(code=1)

    def _get(key):
        if isinstance(details, dict):
            return details.get(key)
        return None

    amount = _get("amount")
    provider = _get("provider")
    deadline = _get("deadline")
    settled = _get("settled")
    typer.echo(f"Deal escrow {escrow_id}")
    typer.echo(f"  amount   : {int(amount) / 10_000_000:.7f} XLM" if amount is not None else "  amount   : —")
    typer.echo(f"  provider : {getattr(provider, 'address', provider)}")
    typer.echo(f"  deadline : {int(deadline)} (unix)" if deadline is not None else "  deadline : —")
    typer.echo(f"  settled  : {bool(settled)}")
