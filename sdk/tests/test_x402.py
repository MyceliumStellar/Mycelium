"""Offline tests for the x402 escrow router."""

import os
from decimal import Decimal

import pytest

from mycelium_sdk import EscrowPaymentRouter, EscrowPaymentManager
from mycelium_sdk.x402 import settlement


class _FakeContext:
    def __init__(self, network_type="testnet"):
        self.calls = []
        self.network_type = network_type
        self.keypair = type("KP", (), {"public_key": "G" + "A" * 55, "secret": "S" + "B" * 55})()
        self.read_returns = {}  # function_name -> canned read-only return

    def call_contract(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("read_only"):
            return self.read_returns.get(kwargs.get("function_name"))
        return type("TxResult", (), {"hash": "abc", "status": "SUCCESS"})()


def test_bundled_escrow_wasm_present():
    # The compiled escrow contract must ship with the SDK for deployment.
    assert os.path.exists(settlement._ESCROW_WASM), settlement._ESCROW_WASM


def test_create_locked_escrow_locks_funds(monkeypatch):
    ctx = _FakeContext()
    router = EscrowPaymentRouter(ctx)
    # Avoid a real on-chain deploy; just return a fake escrow id.
    monkeypatch.setattr(router, "_deploy_escrow_instance", lambda: "CESCROWID")

    escrow_id = router.create_locked_escrow("CPROVIDER", Decimal("1.5"), b"taskhash")
    assert escrow_id == "CESCROWID"

    call = ctx.calls[0]
    assert call["contract_id"] == "CESCROWID"
    assert call["function_name"] == "initialize"
    depositor, provider, token, amount, task_hash, timeout = call["args"]
    assert depositor == ctx.keypair.public_key
    assert provider == "CPROVIDER"
    assert amount == 15_000_000          # 1.5 XLM in stroops
    assert task_hash == b"taskhash"
    # Default token is the network's native SAC.
    from mycelium_sdk.constants import native_token_address
    assert token == native_token_address("testnet")


def test_deploy_escrow_instance_uses_pure_python_deploy(monkeypatch):
    """_deploy_escrow_instance must delegate to context.deploy_contract with the
    bundled escrow WASM bytes — no stellar-cli subprocess."""
    captured = {}

    class _Ctx(_FakeContext):
        def deploy_contract(self, wasm_bytes):
            captured["wasm"] = wasm_bytes
            return "CESCROWDEPLOYED"

    router = EscrowPaymentRouter(_Ctx())
    escrow_id = router._deploy_escrow_instance()
    assert escrow_id == "CESCROWDEPLOYED"
    with open(settlement._ESCROW_WASM, "rb") as f:
        assert captured["wasm"] == f.read()


def test_release_funds_calls_claim():
    ctx = _FakeContext()
    EscrowPaymentRouter(ctx).release_funds("CESCROW", b"proof")
    assert ctx.calls[0]["function_name"] == "claim_funds"
    assert ctx.calls[0]["args"] == [b"proof"]


def test_split_release_computes_exact_amounts():
    """N-way split must invoke claim_and_split with amounts summing exactly to
    the locked amount (remainder absorbed by the last recipient)."""
    ctx = _FakeContext()
    # get_details (read-only) returns the locked amount; claim_and_split records.
    ctx.read_returns = {"get_details": {"amount": 1_000_003}}

    router = EscrowPaymentRouter(ctx)
    router.split_release(
        "CESCROW",
        [("CPROV1", 3333), ("CPROV2", 3333), ("CPROV3", 3334)],
        b"proof",
    )

    split = [c for c in ctx.calls if c["function_name"] == "claim_and_split"][0]
    proof, recipients, amounts = split["args"]
    assert proof == b"proof"
    assert recipients == ["CPROV1", "CPROV2", "CPROV3"]
    # amounts sum exactly to the locked amount, no dust lost
    assert sum(amounts) == 1_000_003
    assert amounts[0] == 1_000_003 * 3333 // 10000
    assert amounts[2] == 1_000_003 - amounts[0] - amounts[1]


def test_split_release_rejects_unbalanced_shares():
    ctx = _FakeContext()
    with pytest.raises(ValueError):
        EscrowPaymentRouter(ctx).split_release("CESCROW", [("CPROV1", 6000), ("CPROV2", 3000)], b"p")


def test_refund_calls_refund():
    ctx = _FakeContext()
    EscrowPaymentRouter(ctx).refund("CESCROW")
    assert ctx.calls[0]["function_name"] == "refund"


def test_manager_alias_back_compat():
    ctx = _FakeContext()
    mgr = EscrowPaymentManager(ctx)
    assert isinstance(mgr, EscrowPaymentRouter)
    assert mgr.disburse_payment("CESCROW", "proofstr") is True
