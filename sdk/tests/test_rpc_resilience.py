"""Offline tests for RPC retry/backoff + idempotent submit (no network)."""

import pytest

from mycelium_sdk import rpc as rpc_helpers


def test_is_transient_classification():
    assert rpc_helpers.is_transient(RuntimeError("TRY_AGAIN_LATER: congested"))
    assert rpc_helpers.is_transient(RuntimeError("HTTP 503 Service Unavailable"))
    assert rpc_helpers.is_transient(RuntimeError("connection reset by peer"))
    # Permanent failures must NOT be retried.
    assert not rpc_helpers.is_transient(RuntimeError("contract reverted, #2"))
    assert not rpc_helpers.is_transient(ValueError("bad signature"))


def test_with_retry_succeeds_after_transient(monkeypatch):
    monkeypatch.setattr(rpc_helpers.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("TRY_AGAIN_LATER")
        return "ok"

    assert rpc_helpers.with_retry(flaky, base_delay=0, label="t") == "ok"
    assert calls["n"] == 3


def test_with_retry_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr(rpc_helpers.time, "sleep", lambda *_: None)

    def always_transient():
        raise RuntimeError("timeout")

    with pytest.raises(RuntimeError, match="timeout"):
        rpc_helpers.with_retry(always_transient, retries=2, base_delay=0, label="t")


def test_with_retry_does_not_retry_permanent(monkeypatch):
    monkeypatch.setattr(rpc_helpers.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def permanent():
        calls["n"] += 1
        raise ValueError("contract error #5")

    with pytest.raises(ValueError):
        rpc_helpers.with_retry(permanent, retries=5, base_delay=0, label="t")
    assert calls["n"] == 1  # never retried


class _FakeStatus:
    """Stand-in for stellar_sdk SendTransactionStatus enum members."""

    PENDING = "PENDING"
    DUPLICATE = "DUPLICATE"
    TRY_AGAIN_LATER = "TRY_AGAIN_LATER"
    ERROR = "ERROR"


class _Send:
    def __init__(self, status, hash="abc", error_result_xdr=None):
        self.status = status
        self.hash = hash
        self.error_result_xdr = error_result_xdr


@pytest.fixture
def patch_status(monkeypatch):
    """Patch the lazily-imported SendTransactionStatus used by submit_transaction."""
    import sys
    import types

    fake_mod = types.ModuleType("stellar_sdk.soroban_rpc")
    fake_mod.SendTransactionStatus = _FakeStatus
    monkeypatch.setitem(sys.modules, "stellar_sdk.soroban_rpc", fake_mod)
    monkeypatch.setattr(rpc_helpers.time, "sleep", lambda *_: None)


def test_submit_returns_on_pending(patch_status):
    class RPC:
        def send_transaction(self, tx):
            return _Send(_FakeStatus.PENDING, hash="h1")

    send = rpc_helpers.submit_transaction(RPC(), object())
    assert send.hash == "h1"


def test_submit_resends_same_tx_on_try_again_later(patch_status):
    """TRY_AGAIN_LATER status loops, re-sending the SAME tx, until PENDING."""
    seq = [_FakeStatus.TRY_AGAIN_LATER, _FakeStatus.TRY_AGAIN_LATER, _FakeStatus.PENDING]
    sent = {"tx": [], "i": 0}
    signed_tx = object()

    class RPC:
        def send_transaction(self, tx):
            sent["tx"].append(tx)
            status = seq[sent["i"]]
            sent["i"] += 1
            return _Send(status, hash="same-hash")

    send = rpc_helpers.submit_transaction(RPC(), signed_tx, base_delay=0)
    assert send.status == _FakeStatus.PENDING
    # Same signed tx object every time — idempotent, hash never changes.
    assert all(tx is signed_tx for tx in sent["tx"])
    assert len(sent["tx"]) == 3


def test_submit_raises_on_error_status(patch_status):
    class RPC:
        def send_transaction(self, tx):
            return _Send(_FakeStatus.ERROR, error_result_xdr="boom")

    with pytest.raises(RuntimeError, match="rejected"):
        rpc_helpers.submit_transaction(RPC(), object())
