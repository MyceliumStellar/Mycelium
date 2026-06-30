"""
Live testnet round-trip (register + resolve against the deployed Hive Registry).

Gated behind MYCELIUM_LIVE_TESTS=1 because it funds an account via Friendbot and
submits real Soroban transactions (network + ~30s).

    MYCELIUM_LIVE_TESTS=1 pytest sdk/tests/test_live_testnet.py
"""

import json
import os
import time

import pytest

LIVE = os.environ.get("MYCELIUM_LIVE_TESTS") == "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="set MYCELIUM_LIVE_TESTS=1 to run")


def test_register_and_resolve(tmp_path):
    import requests
    from stellar_sdk import Keypair

    from mycelium_sdk import crypto, AgentContext, HiveClient
    from mycelium_sdk.constants import FRIENDBOT_URL

    kp = Keypair.random()
    requests.get(f"{FRIENDBOT_URL}/?addr={kp.public_key}", timeout=30)
    time.sleep(5)

    wallet = tmp_path / "wallet.json"
    wallet.write_text(json.dumps({"public_key": kp.public_key, **crypto.encrypt_secret(kp.secret, "pw")}))

    ctx = AgentContext(keypair_path=str(wallet), network_type="testnet", passphrase="pw")
    hive = HiveClient(ctx)

    name = "agent_" + kp.public_key[1:7].lower()
    caps = ["data-analysis", "stellar-arbitrage"]
    endpoint = "https://demo.agents.mycelium.sh/api/v1"

    res = hive.register(name, caps, endpoint)
    assert res.status == "SUCCESS"

    meta = hive.resolve_agent(name)
    assert meta["public_key"] == kp.public_key
    assert meta["endpoint"] == endpoint
    assert meta["capability_hash"] == HiveClient._compute_capability_hash(caps)
    assert meta["reputation"] == 0


def test_escrow_lock_and_claim(tmp_path):
    """
    Deploy an escrow, lock 1 XLM payable to a provider, then claim it with a
    valid proof. Verifies the provider's native balance increases.
    """
    import hashlib
    from decimal import Decimal

    import requests
    from stellar_sdk import Keypair, Server

    from mycelium_sdk import crypto, AgentContext, EscrowPaymentRouter
    from mycelium_sdk.constants import FRIENDBOT_URL, HORIZON_URLS

    depositor = Keypair.random()
    provider = Keypair.random()
    for kp in (depositor, provider):
        requests.get(f"{FRIENDBOT_URL}/?addr={kp.public_key}", timeout=30)
    time.sleep(6)

    wallet = tmp_path / "wallet.json"
    wallet.write_text(
        json.dumps({"public_key": depositor.public_key, **crypto.encrypt_secret(depositor.secret, "pw")})
    )
    ctx = AgentContext(keypair_path=str(wallet), network_type="testnet", passphrase="pw")
    router = EscrowPaymentRouter(ctx)

    # The depositor doubles as the judge here so a single funded signer can both
    # lock and authorize the release; in production the judge is a distinct
    # verdict authority (see PROOF_SYSTEM.md).
    evidence_root = hashlib.sha256(b"delivered-work-bundle").digest()

    def native_balance(public_key: str) -> float:
        acct = Server(HORIZON_URLS["testnet"]).accounts().account_id(public_key).call()
        return next(float(b["balance"]) for b in acct["balances"] if b["asset_type"] == "native")

    before = native_balance(provider.public_key)
    escrow_id = router.create_locked_escrow(provider.public_key, Decimal("1"), depositor.public_key)
    assert escrow_id.startswith("C")

    router.release_funds(escrow_id, evidence_root)
    time.sleep(6)
    assert native_balance(provider.public_key) > before
