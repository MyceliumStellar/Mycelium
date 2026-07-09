"""
`mycelium deal …` — wire two agents into an agent-to-agent (A2A) commerce deal.

Where `mycelium pay` is an *unconditional* transfer, a deal is *conditional*
x402 commerce between two agents: the payer locks funds into a fresh escrow
payable to a provider (resolved by Hive Registry unique name or raw address),
and the provider only collects once a `judge` authorizes release on a passing
verdict of the delivered work. If no release happens, the payer reclaims the
funds after the timeout.

This is the CLI front door to what `a2a_demo.py` / `EscrowPaymentRouter` do by
hand — two sovereign agents transacting purely through on-chain state, with a
judge as the impartial release authority (see PROOF_SYSTEM.md):

  open    payer locks `amount` XLM to a provider, naming a judge → escrow id
  release the judge authorizes payout (passing verdict) → funds disburse
  refund  payer reclaims the locked funds after the deadline passes
  status  read the escrow's current state (amount, provider, judge, settled)

Release follows a judge's verdict rather than a SHA-256 preimage: a hash only
proved the claimant could echo the agreed bytes, never that the work was done.
The board/registry default from `mycelium.toml`, mirroring `deploy` / `job`.

Commands: open, release, refund, status.
"""

import hashlib
import os
from decimal import Decimal
from typing import Optional

import typer
from mycelium_cli.commands import resolve_network

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


def _evidence_root(evidence: str) -> bytes:
    """The 32-byte evidence_root: SHA-256 of a file's bytes or a literal string."""
    if os.path.isfile(evidence):
        with open(evidence, "rb") as f:
            data = f.read()
    else:
        data = evidence.encode("utf-8")
    return hashlib.sha256(data).digest()


@deal_app.command("open")
def open_deal(
    to: str = typer.Option(..., "--to", help="Provider: Hive Registry unique name or G/C address to pay"),
    amount: str = typer.Option(..., "--amount", help="Amount in XLM to lock for the provider"),
    judge: str = typer.Option(..., "--judge", help="Judge: unique name or G/C address — the release authority"),
    token: str = typer.Option(None, "--token", help="Payment token contract (defaults to native XLM SAC)"),
    timeout: int = typer.Option(DEFAULT_TIMEOUT_SECONDS, "--timeout", help="Refund deadline in seconds"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
    registry: str = typer.Option(None, "--registry", help="Hive Registry id override (for name resolution)"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Payer locks `amount` XLM to a provider, naming a judge as the release authority."""
    from mycelium_sdk.x402.settlement import EscrowPaymentRouter

    context = _context(network, wallet, signing=True)
    provider = _resolve_agent_address(context, to, registry)
    judge_addr = _resolve_agent_address(context, judge, registry)

    typer.echo(f"[deal] Locking {amount} XLM to provider {to} ({provider[:8]}…), judge {judge_addr[:8]}…, for {timeout}s...")
    try:
        escrow_id = EscrowPaymentRouter(context).create_locked_escrow(
            provider_id=provider,
            amount_xlm=Decimal(amount),
            judge=judge_addr,
            token=token,
            timeout_seconds=timeout,
        )
    except Exception as e:  # noqa: BLE001
        typer.echo(f"❌ deal open failed: {e}")
        raise typer.Exit(code=1)
    typer.echo(f"✓ Deal opened. Escrow {escrow_id}")
    typer.echo(f"  Judge releases with: mycelium deal release {escrow_id} --evidence <bundle>")
    typer.echo(f"  Payer refunds after {timeout}s with: mycelium deal refund {escrow_id}")


@deal_app.command("release")
def release(
    escrow_id: str = typer.Argument(..., help="Escrow contract id from `deal open`"),
    evidence: str = typer.Option(..., "--evidence", help="Evidence bundle file/string (its SHA-256 is recorded for audit)"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Judge wallet path (the escrow's release authority)"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Judge disburses the locked funds to the provider on a passing verdict."""
    from mycelium_sdk.x402.settlement import EscrowPaymentRouter

    context = _context(network, wallet, signing=True)
    try:
        EscrowPaymentRouter(context).release_funds(escrow_id, _evidence_root(evidence))
    except Exception as e:  # noqa: BLE001
        typer.echo(f"❌ deal release failed: {e}")
        raise typer.Exit(code=1)
    typer.echo(f"✓ Released escrow {escrow_id} — funds disbursed to the provider.")


@deal_app.command("refund")
def refund(
    escrow_id: str = typer.Argument(..., help="Escrow contract id from `deal open`"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
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
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True),
):
    network = resolve_network(network, use_testnet, use_mainnet)
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
    judge = _get("judge")
    deadline = _get("deadline")
    settled = _get("settled")
    typer.echo(f"Deal escrow {escrow_id}")
    typer.echo(f"  amount   : {int(amount) / 10_000_000:.7f} XLM" if amount is not None else "  amount   : —")
    typer.echo(f"  provider : {getattr(provider, 'address', provider)}")
    typer.echo(f"  judge    : {getattr(judge, 'address', judge)}")
    typer.echo(f"  deadline : {int(deadline)} (unix)" if deadline is not None else "  deadline : —")
    typer.echo(f"  settled  : {bool(settled)}")
