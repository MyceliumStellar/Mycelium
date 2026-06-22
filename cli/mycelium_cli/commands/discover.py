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

    print(f"[discover] Scanning Hive Registry {registry} on {network}...")
    try:
        agents = hive.discover_agents(start_ledger=start_ledger, resolve=resolve)
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

    print(f"\nFound {len(agents)} agent(s):\n")
    name_w = max(len("NAME"), *(len(a.get("name", "")) for a in agents))
    addr_w = max(len("ADDRESS"), *(len(a.get("public_key") or "") for a in agents))
    header = f"  {'NAME':<{name_w}}  {'ADDRESS':<{addr_w}}  {'REP':>4}  ENDPOINT"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for a in agents:
        name = a.get("name", "")
        addr = a.get("public_key") or "—"
        rep = a.get("reputation")
        rep_str = str(rep) if rep is not None else "—"
        endpoint = a.get("endpoint") or "—"
        print(f"  {name:<{name_w}}  {addr:<{addr_w}}  {rep_str:>4}  {endpoint}")
    print()
