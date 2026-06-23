"""
`mycelium deploy` — publish the compiled WASM to Stellar/Soroban (sdk.md 2.4).

Mirrors the proven flow in the IDE backend's /api/deploy:
  - testnet: if the wallet has 0 XLM, fund it via Friendbot and poll.
  - mainnet: refuse to deploy unless the wallet holds >= 5 XLM (no Friendbot).
  - deploy via pure-Python signed Soroban transactions (no stellar-cli / Rust).
  - write the resulting contract_id + wallet public key back to mycelium.toml.
"""

import os
import sys
import time

from mycelium_sdk import crypto
from mycelium_sdk.constants import (
    FRIENDBOT_URL,
    HORIZON_URLS,
    MAINNET_MIN_XLM,
    normalize_network,
)

from mycelium_cli.config import get_value, set_value

DEFAULT_WALLET_PATH = os.path.join(".mycelium", "wallet.json")


def _load_secret(wallet_path: str, passphrase: str | None) -> tuple[str, str]:
    """Decrypt the wallet, returning (secret_seed, public_key)."""
    import json

    with open(wallet_path, "r") as f:
        wallet = json.load(f)
    pw = crypto.resolve_passphrase(passphrase)
    secret = crypto.decrypt_secret(
        wallet["encrypted_secret"], wallet["nonce"], wallet["salt"], pw
    )
    return secret, wallet["public_key"]


def _native_balance(network: str, public_key: str) -> float:
    """Return the account's native XLM balance, or 0.0 if the account is new."""
    from stellar_sdk import Server
    from stellar_sdk.exceptions import NotFoundError

    server = Server(HORIZON_URLS[network])
    try:
        acct = server.accounts().account_id(public_key).call()
    except NotFoundError:
        return 0.0
    for bal in acct.get("balances", []):
        if bal.get("asset_type") == "native":
            return float(bal["balance"])
    return 0.0


def _fund_with_friendbot(network: str, public_key: str) -> None:
    import requests

    print(f"[deploy] Wallet has 0 XLM — requesting Friendbot funding for {public_key}...")
    res = requests.get(f"{FRIENDBOT_URL}/?addr={public_key}", timeout=20)
    if not res.ok:
        raise RuntimeError(f"Friendbot funding failed: HTTP {res.status_code}")

    # Poll the ledger until the funding actually lands (spec: "Poll until confirmed").
    deadline = time.time() + 30
    while time.time() < deadline:
        time.sleep(3)
        if _native_balance(network, public_key) > 0.0:
            print("[deploy] Friendbot funding confirmed on-chain.")
            return
    raise RuntimeError(
        "Friendbot returned success but the account is still unfunded after 30s. "
        "Retry the deploy, or fund the account manually."
    )


def run_deploy(
    network: str = "testnet",
    wasm_path: str | None = None,
    wallet_path: str = DEFAULT_WALLET_PATH,
    passphrase: str | None = None,
    write_config: bool = True,
) -> str:
    """Deploy the compiled contract and return the new contract id."""
    network = normalize_network(network)
    wasm_path = wasm_path or get_value("onchain", "target_wasm", "build/contract.wasm")

    if not os.path.exists(wasm_path):
        print(f"Error: compiled WASM {wasm_path} not found. Run `mycelium compile` first.")
        sys.exit(1)
    if not os.path.exists(wallet_path):
        print(f"Error: wallet {wallet_path} not found. Run `mycelium newwallet` first.")
        sys.exit(1)

    _, public_key = _load_secret(wallet_path, passphrase)
    print(f"[deploy] Deploying {wasm_path} to {network} as {public_key}...")

    balance = _native_balance(network, public_key)
    if network == "testnet":
        if balance <= 0.0:
            _fund_with_friendbot(network, public_key)
    else:
        if balance < MAINNET_MIN_XLM:
            print(
                f"[Error] Insufficient funds for live deployment. Mainnet operations "
                f"require at least {int(MAINNET_MIN_XLM)} XLM sequence reserve. "
                f"Balance must be deposited to: {public_key}."
            )
            sys.exit(1)

    with open(wasm_path, "rb") as f:
        wasm_bytes = f.read()

    from mycelium_sdk.context import AgentContext

    try:
        ctx = AgentContext(
            keypair_path=wallet_path,
            network_type=network,
            passphrase=passphrase,
        )
        contract_id = ctx.deploy_contract(wasm_bytes)
    except Exception as e:  # noqa: BLE001 - surface a clean CLI error
        print(f"❌ Deployment failed: {e}")
        sys.exit(1)

    print(f"✓ Deployment successful! Contract ID: {contract_id}")

    if write_config:
        try:
            set_value("onchain", "contract_id", contract_id)
            set_value("onchain", "wallet_public_key", public_key)
            print("  Wrote contract_id + wallet_public_key to mycelium.toml")
        except FileNotFoundError:
            pass  # deploying outside a project dir is allowed

    return contract_id
