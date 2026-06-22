"""
`mycelium pay <name|address> <amount>` — machine-to-machine XLM settlement.

Wraps what a2a_demo.py does by hand: resolve the recipient (a Hive Registry
unique name, or a raw G... address) to an address, then sign and submit a native
XLM payment from the project wallet. Reputation/endpoint come from the registry
so you can pay an agent by *name* without knowing its address.
"""

import os
import sys
from typing import Optional

from mycelium_cli.config import get_value

DEFAULT_WALLET_PATH = ".mycelium/wallet.json"


def _resolve_recipient(recipient: str, network: str, registry: Optional[str]) -> str:
    """Return a destination G-address for a name or a raw address."""
    from stellar_sdk import StrKey

    if StrKey.is_valid_ed25519_public_key(recipient):
        return recipient

    from mycelium_sdk import AgentContext, HiveClient

    print(f"[pay] Resolving '{recipient}' via Hive Registry...")
    ro = AgentContext.read_only(network_type=network)
    entry = HiveClient(ro, registry_address=registry).resolve_agent(recipient)
    dest = entry.get("public_key")
    if not dest:
        print(f"❌ '{recipient}' resolved to no address.")
        sys.exit(1)
    print(f"[pay] '{recipient}' -> {dest}")
    return dest


def run_pay(
    recipient: str,
    amount: str,
    network: Optional[str] = None,
    wallet_path: str = DEFAULT_WALLET_PATH,
    passphrase: Optional[str] = None,
) -> str:
    from stellar_sdk import Asset, TransactionBuilder

    from mycelium_sdk import AgentContext
    from mycelium_sdk.constants import HIVEMIND_REGISTRY_ADDRESS

    network = network or get_value("onchain", "network", "testnet")
    registry = get_value("registry", "hive_registry_address") or HIVEMIND_REGISTRY_ADDRESS

    if not os.path.exists(wallet_path):
        print(f"Error: wallet {wallet_path} not found. Run `mycelium newwallet` first.")
        sys.exit(1)

    dest = _resolve_recipient(recipient, network, registry)

    context = AgentContext(keypair_path=wallet_path, network_type=network, passphrase=passphrase)
    print(f"[pay] Sending {amount} XLM to {dest} as {context.keypair.public_key}...")

    try:
        source = context.horizon_server.load_account(context.keypair.public_key)
        tx = (
            TransactionBuilder(source, context.network_passphrase, base_fee=100)
            .append_payment_op(destination=dest, asset=Asset.native(), amount=str(amount))
            .set_timeout(60)
            .build()
        )
        tx.sign(context.keypair)
        resp = context.horizon_server.submit_transaction(tx)
    except Exception as e:
        print(f"❌ Payment failed: {e}")
        sys.exit(1)

    if not resp.get("successful", False):
        print(f"❌ Payment rejected: {resp.get('result_xdr')}")
        sys.exit(1)

    tx_hash = resp["hash"]
    print(f"✓ Paid {amount} XLM to {recipient}. Tx: {tx_hash}")
    return tx_hash
