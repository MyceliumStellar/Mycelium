"""Offline tests for the typed ContractClient and async call path (no network)."""

import asyncio
from types import SimpleNamespace

import pytest

from mycelium_sdk import spec as spec_mod
from mycelium_sdk.context import AgentContext
from mycelium_sdk.contract_client import ContractClient


class _FakeContext:
    """Records calls; spec fetch is unavailable by default (no validation)."""

    def __init__(self):
        self.calls = []
        self.acalls = []
        self.soroban_rpc = object()

    def call_contract(self, contract_id, function_name, args, read_only=False):
        self.calls.append((contract_id, function_name, list(args), read_only))
        return SimpleNamespace(return_value=99)

    async def acall_contract(self, contract_id, function_name, args, read_only=False):
        self.acalls.append((contract_id, function_name, list(args), read_only))
        return SimpleNamespace(return_value=99)


def test_state_changing_method_dispatch():
    ctx = _FakeContext()
    client = ContractClient(ctx, "CID1")
    client.add(40)
    assert ctx.calls == [("CID1", "add", [40], False)]


def test_read_namespace_is_read_only():
    ctx = _FakeContext()
    ContractClient(ctx, "CID2").read.get_count()
    assert ctx.calls == [("CID2", "get_count", [], True)]


def test_async_dispatch_write_and_read():
    ctx = _FakeContext()
    client = ContractClient(ctx, "CID3")
    asyncio.run(client.aio.add(40))
    asyncio.run(client.aio.read.get_count())
    assert ctx.acalls == [
        ("CID3", "add", [40], False),
        ("CID3", "get_count", [], True),
    ]


def test_spec_validation_rejects_unknown_function(monkeypatch):
    monkeypatch.setattr(spec_mod, "fetch_function_names", lambda rpc, cid: ["add", "get_count"])
    client = ContractClient(_FakeContext(), "CID4")
    client.add(1)  # known -> fine
    with pytest.raises(AttributeError, match="no function 'nope'"):
        client.nope()
    # dir() surfaces the real functions for autocomplete.
    assert "add" in dir(client) and "get_count" in dir(client)


def test_no_validation_when_spec_unavailable(monkeypatch):
    monkeypatch.setattr(spec_mod, "fetch_function_names", lambda rpc, cid: None)
    ctx = _FakeContext()
    # Unknown name is allowed through to the contract's own validation.
    ContractClient(ctx, "CID5").anything_goes(7)
    assert ctx.calls == [("CID5", "anything_goes", [7], False)]


def test_contract_id_property():
    assert ContractClient(_FakeContext(), "CIDX").contract_id == "CIDX"


def test_acall_contract_runs_sync_path_off_thread():
    """AgentContext.acall_contract delegates to call_contract via a worker thread."""
    ctx = AgentContext.__new__(AgentContext)
    recorded = []

    def fake_call(contract_id, function_name, args, read_only=False):
        recorded.append((contract_id, function_name, list(args), read_only))
        return "ok"

    ctx.call_contract = fake_call  # instance attr shadows the method
    result = asyncio.run(ctx.acall_contract("CID", "add", [5], read_only=True))
    assert result == "ok"
    assert recorded == [("CID", "add", [5], True)]


def test_contract_factory_returns_client():
    ctx = AgentContext.__new__(AgentContext)
    ctx.soroban_rpc = object()
    client = AgentContext.contract(ctx, "CIDF")
    assert isinstance(client, ContractClient)
    assert client.contract_id == "CIDF"
