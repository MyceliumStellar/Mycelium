"""
`mycelium resolve <name>` — look up a single agent in the Hive Registry.

Read-only and wallet-free: resolves a unique agent name to its on-chain
directory entry (address, capability hash, endpoint, reputation).
"""

import sys
from typing import Optional

# The HiveRegistry contract reverts with ContractError.NOT_REGISTERED (code 2)
# when a name has never been registered.
_NOT_REGISTERED_MARKERS = ("contract, #2)", "not registered", "notregistered", "keyerror")


def _is_not_registered(error: Exception) -> bool:
    m = str(error).lower()
    return any(marker in m for marker in _NOT_REGISTERED_MARKERS)


def run_resolve(
    name: str,
    network: Optional[str] = None,
    registry: Optional[str] = None,
) -> dict:
    from mycelium_sdk import AgentContext, HiveClient
    from mycelium_sdk.constants import HIVEMIND_REGISTRY_ADDRESS

    from mycelium_cli.config import get_value

    network = network or get_value("onchain", "network", "testnet")
    registry = registry or get_value("registry", "hive_registry_address") or HIVEMIND_REGISTRY_ADDRESS

    context = AgentContext.read_only(network_type=network)
    hive = HiveClient(context, registry_address=registry)

    print(f"[resolve] Looking up '{name}' on registry {registry} ({network})...")
    try:
        entry = hive.resolve_agent(name)
    except Exception as e:
        if _is_not_registered(e):
            print(f"❌ '{name}' is not registered in the Hive Registry.")
        else:
            print(f"❌ Resolution failed: {e}")
        sys.exit(1)

    cap = entry.get("capability_hash")
    cap_str = cap.hex() if isinstance(cap, (bytes, bytearray)) else (cap or "—")
    print(f"\n✓ {name}")
    print(f"  address     : {entry.get('public_key') or '—'}")
    print(f"  endpoint    : {entry.get('endpoint') or '—'}")
    print(f"  reputation  : {entry.get('reputation', 0)}")
    print(f"  capability  : {cap_str}\n")
    return entry
