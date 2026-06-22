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

    def call_contract(self, **kwargs):
        self.calls.append(kwargs)
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


def test_release_funds_calls_claim():
    ctx = _FakeContext()
    EscrowPaymentRouter(ctx).release_funds("CESCROW", b"proof")
    assert ctx.calls[0]["function_name"] == "claim_funds"
    assert ctx.calls[0]["args"] == [b"proof"]


def test_refund_calls_refund():
    ctx = _FakeContext()
    EscrowPaymentRouter(ctx).refund("CESCROW")
    assert ctx.calls[0]["function_name"] == "refund"


def test_manager_alias_back_compat():
    ctx = _FakeContext()
    mgr = EscrowPaymentManager(ctx)
    assert isinstance(mgr, EscrowPaymentRouter)
    assert mgr.disburse_payment("CESCROW", "proofstr") is True
