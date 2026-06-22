"""
`mycelium fund` — top up a testnet wallet from Friendbot.

Today funding only happens implicitly as a `mycelium deploy` side-effect when the
wallet is empty. This makes it explicit: `mycelium fund` requests Friendbot
lumens for the project wallet (or any --address) and polls until the funding
lands on-chain. Friendbot is testnet-only; mainnet wallets must be funded out of
band, so this refuses to run there.
"""

import json
import os
import sys
from typing import Optional

from mycelium_sdk.constants import normalize_network

from mycelium_cli.config import get_value
from mycelium_cli.commands.deploy import (
    DEFAULT_WALLET_PATH,
    _fund_with_friendbot,
    _native_balance,
)


def _wallet_public_key(wallet_path: str) -> Optional[str]:
    """Read the wallet's plaintext public key (no passphrase needed)."""
    if not os.path.exists(wallet_path):
        return None
    with open(wallet_path, "r") as f:
        return json.load(f).get("public_key")


def run_fund(
    address: Optional[str] = None,
    network: Optional[str] = None,
    wallet_path: str = DEFAULT_WALLET_PATH,
) -> float:
    """Fund `address` (or the project wallet) via Friendbot. Returns the new balance."""
    network = normalize_network(network or get_value("onchain", "network", "testnet"))
    if network != "testnet":
        print("❌ Friendbot funding is testnet-only. Fund mainnet wallets manually.")
        sys.exit(1)

    public_key = address or _wallet_public_key(wallet_path)
    if not public_key:
        print(
            f"Error: no address given and wallet {wallet_path} not found.\n"
            "  Run `mycelium newwallet` first, or pass --address G..."
        )
        sys.exit(1)

    balance = _native_balance(network, public_key)
    print(f"[fund] {public_key} currently holds {balance} XLM on {network}.")
    try:
        _fund_with_friendbot(network, public_key)
    except Exception as e:
        print(f"❌ Funding failed: {e}")
        sys.exit(1)

    new_balance = _native_balance(network, public_key)
    print(f"✓ Funded. Balance: {new_balance} XLM (+{new_balance - balance:.4f}).")
    return new_balance
