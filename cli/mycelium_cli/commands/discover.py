"""
`mycelium agents` — discover every agent registered on the Hive Registry.

This is a read-only, wallet-free command: anyone can list the global agent
directory without owning a wallet or spending fees. It scans the registry
contract's `agent_registered` events over the RPC's retained ledger window and
resolves each name to its current address, endpoint, and reputation.

The registry address comes from `[registry].hive_registry_address` in
mycelium.toml when run inside a project, falling back to the SDK default.
"""

import sys
from typing import Optional


def run_discover(
    network: Optional[str] = None,
    registry: Optional[str] = None,
    start_ledger: Optional[int] = None,
    resolve: bool = True,
) -> list:
    from mycelium_sdk import AgentContext, HiveClient
    from mycelium_sdk.constants import HIVEMIND_REGISTRY_ADDRESS

    from mycelium_cli.config import get_value

    network = network or get_value("onchain", "network", "testnet")
    registry = registry or get_value("registry", "hive_registry_address") or HIVEMIND_REGISTRY_ADDRESS

    context = AgentContext.read_only(network_type=network)
    hive = HiveClient(context, registry_address=registry)

    # Hosted indexer first (instant, full history); falls back to the on-chain
    # event-scan automatically when unreachable. An explicit --start-ledger means
    # the caller wants the chain scan, so skip the indexer in that case.
    prefer_indexer = start_ledger is None
    if prefer_indexer:
        print(f"[discover] Querying indexer (falls back to chain scan of {registry})...")
    else:
        print(f"[discover] Scanning Hive Registry {registry} on {network}...")
    try:
        agents = hive.discover_agents(
            start_ledger=start_ledger, resolve=resolve, prefer_indexer=prefer_indexer
        )
    except Exception as e:
        print(f"❌ Discovery failed: {e}")
        sys.exit(1)

    _print_agents(agents)
    return agents


def _print_agents(agents: list) -> None:
    if not agents:
        print(
            "No agents found in the registry's retained event window.\n"
            "  (Registrations older than the RPC's retention horizon are not "
            "discoverable — pass --start-ledger to widen the scan.)"
        )
        return

    from mycelium_sdk.banner import get_terminal_columns
    columns = get_terminal_columns()

    # If the console window is narrower than ~120 characters, truncate the 56-char
    # public keys (e.g., GCBFVJZF...OLTZHQ) to prevent horizontal line wrapping/clipping.
    truncate_addr = columns < 120

    from rich.console import Console
    from rich.table import Table

    console = Console(width=columns)

    table = Table(
        title=f"[bold green]Hive Registered Agents[/bold green] (found {len(agents)})",
        title_justify="left",
        border_style="green",
        show_lines=False
    )
    table.add_column("Name", style="bold cyan", no_wrap=True)
    table.add_column("Address", style="dim white")
    table.add_column("Rep", style="magenta", justify="right")
    table.add_column("Endpoint", style="blue")

    for a in agents:
        name = a.get("name", "")
        addr = a.get("public_key") or "—"
        if truncate_addr and len(addr) == 56:
            addr = f"{addr[:8]}...{addr[-8:]}"
        rep = a.get("reputation")
        rep_str = str(rep) if rep is not None else "—"
        endpoint = a.get("endpoint") or "—"
        table.add_row(name, addr, rep_str, endpoint)

    console.print()
    console.print(table)
    console.print()
