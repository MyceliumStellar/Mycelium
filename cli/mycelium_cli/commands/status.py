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

    print(f"\nMycelium status — project '{project}' ({framework})\n")

    # Wallet + funding.
    if public_key:
        bal_str = f"{balance} XLM" if balance is not None else "unknown (RPC unreachable)"
        funded = balance is not None and balance > 0
        print(f"  {_OK} wallet      {public_key}")
        print(f"  {_OK if funded else _NO} balance     {bal_str}")
    else:
        print(f"  {_NO} wallet      not found — run `mycelium newwallet`")
        print(f"  {_NO} balance     —")

    print(f"  •  network     {network}")

    # Deploy state.
    if contract_id:
        print(f"  {_OK} contract    {contract_id}")
    else:
        print(f"  {_NO} contract    not deployed — run `mycelium deploy`")

    # Registry registration.
    if not unique_name:
        print(f"  {_NO} registry    no [agent].unique_name in mycelium.toml")
    elif entry and entry.get("public_key"):
        rep = entry.get("reputation", 0)
        print(f"  {_OK} registry    '{unique_name}' registered (reputation {rep})")
        if entry.get("endpoint"):
            print(f"               endpoint {entry['endpoint']}")
    else:
        print(f"  {_NO} registry    '{unique_name}' not registered — run `mycelium register`")

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
    print(f"\n  Next: {nxt}\n" if nxt else "\n  Everything is set up. 🍄\n")

    return {
        "public_key": public_key,
        "balance": balance,
        "network": network,
        "contract_id": contract_id,
        "registered": bool(entry and entry.get("public_key")),
    }
