"""
`mycelium status` — the "am I set up correctly?" command.

One screen that answers, for the current project: which wallet am I, how much
XLM do I hold, on which network, is my contract deployed, and am I registered in
the Hive Registry (with what reputation). Everything here is read-only and
wallet-free — it never prompts for a passphrase or spends a fee.
"""

import json
import os
import sys
from typing import Optional

from mycelium_cli.config import get_value, load_config

DEFAULT_WALLET_PATH = os.path.join(".mycelium", "wallet.json")

_OK = "✓"
_NO = "✗"


def _wallet_public_key(wallet_path: str) -> Optional[str]:
    if not os.path.exists(wallet_path):
        return None
    try:
        with open(wallet_path, "r", encoding="utf-8") as f:
            return json.load(f).get("public_key")
    except (OSError, json.JSONDecodeError):
        return None


def _safe_balance(network: str, public_key: str) -> Optional[float]:
    try:
        from mycelium_cli.commands.deploy import _native_balance

        return _native_balance(network, public_key)
    except Exception:
        return None


def _registry_entry(network: str, registry: Optional[str], name: str) -> Optional[dict]:
    """Resolve `name` in the registry, returning the entry or None if unregistered."""
    try:
        from mycelium_sdk import AgentContext, HiveClient

        context = AgentContext.read_only(network_type=network)
        hive = HiveClient(context, registry_address=registry)
        return hive.resolve_agent(name)
    except Exception:
        return None


def run_status(
    network: Optional[str] = None,
    wallet_path: str = DEFAULT_WALLET_PATH,
) -> dict:
    try:
        load_config()
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)

    from mycelium_sdk.constants import HIVEMIND_REGISTRY_ADDRESS

    project = get_value("project", "name", "—")
    unique_name = get_value("agent", "unique_name")
    framework = get_value("agent", "framework", "—")
    network = network or get_value("onchain", "network", "testnet")
    contract_id = get_value("onchain", "contract_id") or ""
    registry = get_value("registry", "hive_registry_address") or HIVEMIND_REGISTRY_ADDRESS

    public_key = _wallet_public_key(wallet_path)
    balance = _safe_balance(network, public_key) if public_key else None
    entry = _registry_entry(network, registry, unique_name) if unique_name else None

    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    console = Console()

    table = Table(show_header=False, box=None, padding=(0, 2))

    # Wallet + funding.
    if public_key:
        bal_str = f"[bold green]{balance} XLM[/bold green]" if balance is not None else "[dim]unknown (RPC unreachable)[/dim]"
        funded = balance is not None and balance > 0
        table.add_row(
            "[bold green]✓[/bold green] [bold]wallet[/bold]",
            f"[cyan]{public_key}[/cyan]"
        )
        table.add_row(
            "[bold green]✓[/bold green] [bold]balance[/bold]" if funded else "[bold red]✗[/bold red] [bold]balance[/bold]",
            bal_str
        )
    else:
        table.add_row(
            "[bold red]✗[/bold red] [bold]wallet[/bold]",
            "[bold red]not found[/bold red] — run `mycelium newwallet`"
        )
        table.add_row(
            "[bold red]✗[/bold red] [bold]balance[/bold]",
            "[dim]—[/dim]"
        )

    table.add_row(
        "  [bold cyan]•[/bold cyan] [bold]network[/bold]",
        f"[magenta]{network}[/magenta]"
    )

    # Deploy state.
    if contract_id:
        table.add_row(
            "[bold green]✓[/bold green] [bold]contract[/bold]",
            f"[cyan]{contract_id}[/cyan]"
        )
    else:
        table.add_row(
            "[bold red]✗[/bold red] [bold]contract[/bold]",
            "[bold red]not deployed[/bold red] — run `mycelium deploy`"
        )

    # Registry registration.
    if not unique_name:
        table.add_row(
            "[bold red]✗[/bold red] [bold]registry[/bold]",
            "[bold red]no [agent].unique_name in mycelium.toml[/bold red]"
        )
    elif entry and entry.get("public_key"):
        rep = entry.get("reputation", 0)
        reg_details = f"[bold green]'{unique_name}' registered[/bold green] (reputation {rep})"
        if entry.get("endpoint"):
            reg_details += f"\n[dim]endpoint: {entry['endpoint']}[/dim]"
        table.add_row(
            "[bold green]✓[/bold green] [bold]registry[/bold]",
            reg_details
        )
    else:
        table.add_row(
            "[bold red]✗[/bold red] [bold]registry[/bold]",
            f"[bold red]'{unique_name}' not registered[/bold red] — run `mycelium register`"
        )

    panel = Panel(
        table,
        title=f"[bold green]Mycelium Status[/bold green] — [bold cyan]{project}[/bold cyan] ({framework})",
        title_align="left",
        border_style="green",
        padding=(1, 2)
    )

    console.print()
    console.print(panel)

    # One-line "what next?" nudge.
    if not public_key:
        nxt = "mycelium newwallet"
    elif not balance:
        nxt = "mycelium fund"
    elif not contract_id:
        nxt = "mycelium compile && mycelium deploy"
    elif not (entry and entry.get("public_key")):
        nxt = "mycelium register"
    else:
        nxt = None

    if nxt:
        next_panel = Panel(
            Text(nxt, style="bold yellow"),
            title="[bold yellow]Next Step[/bold yellow]",
            title_align="left",
            border_style="yellow",
            expand=False
        )
        console.print(next_panel)
        console.print()
    else:
        console.print("\n  [bold green]Everything is set up. 🍄[/bold green]\n")

    return {
        "public_key": public_key,
        "balance": balance,
        "network": network,
        "contract_id": contract_id,
        "registered": bool(entry and entry.get("public_key")),
    }
