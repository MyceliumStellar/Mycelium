"""
`mycelium register` — register the agent's unique name on the Hive Registry
(sdk.md section 2.5).

Reads the agent metadata from mycelium.toml, builds an AgentContext from the
local wallet, and invokes `register_agent` on the registry contract. The
registry address comes from `[registry].hive_registry_address` (falling back to
the SDK default). If that address is not a deployed registry, the on-chain call
fails loudly — no fabricated success.
"""

import os
import sys

DEFAULT_WALLET_PATH = os.path.join(".mycelium", "wallet.json")


def run_register(
    network: str | None = None,
    wallet_path: str = DEFAULT_WALLET_PATH,
    passphrase: str | None = None,
) -> str:
    from mycelium_sdk import AgentContext, HiveClient

    from mycelium_cli.config import get_value

    unique_name = get_value("agent", "unique_name")
    if not unique_name:
        print("Error: [agent].unique_name missing from mycelium.toml.")
        sys.exit(1)
    endpoint = get_value("registry", "service_endpoint", "")
    capabilities = get_value("registry", "capabilities", []) or []
    registry_address = get_value("registry", "hive_registry_address")
    network = network or get_value("onchain", "network", "testnet")
    model = get_value("agent", "model", "custom")
    role = get_value("agent", "role", "Autonomous Agent")
    description = get_value("agent", "description", "Custom on-chain agent resolved from Hive Registry.")

    if not os.path.exists(wallet_path):
        print(f"Error: wallet {wallet_path} not found. Run `mycelium newwallet` first.")
        sys.exit(1)

    context = AgentContext(keypair_path=wallet_path, network_type=network, passphrase=passphrase)
    hive = HiveClient(context, registry_address=registry_address)

    print(f"[register] Registering '{unique_name}' on registry {hive.registry_address}...")
    try:
        result = hive.register(unique_name, capabilities, endpoint, model=model, role=role, desc=description)
    except Exception as e:
        m = str(e).lower()
        if "nametaken" in m or ("name" in m and ("taken" in m or "collision" in m or "exists" in m)):
            print(f"❌ Registration failed: the name '{unique_name}' is already taken.")
        else:
            print(f"❌ Registration failed: {e}")
        sys.exit(1)

    tx_hash = getattr(result, "hash", str(result))
    print(f"✓ Registered '{unique_name}'. Tx: {tx_hash}")
    return tx_hash
