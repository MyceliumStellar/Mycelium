"""Offline tests for the AI orchestrator adapters (no optional extras required)."""

import importlib.util

import pytest

from mycelium_sdk.adapters import anthropic, gemini, langgraph


class _FakeContext:
    def __init__(self):
        self.calls = []

    def call_contract(self, contract_id, function_name, args, read_only=False):
        self.calls.append((contract_id, function_name, args, read_only))
        return type("TxResult", (), {"hash": "h", "return_value": 42})()


# ── anthropic (schema builder needs no extra) ────────────────────────────────
def test_anthropic_schema_with_fixed_contract():
    s = anthropic.contract_tool_schema("trade", "Execute a trade", contract_id="C1")
    assert s["name"] == "trade"
    assert "contract_id" not in s["input_schema"]["properties"]


def test_anthropic_schema_requires_contract_when_unbound():
    s = anthropic.contract_tool_schema("trade", "Execute a trade")
    assert "contract_id" in s["input_schema"]["properties"]
    assert "contract_id" in s["input_schema"]["required"]


def test_anthropic_dispatcher_invokes_contract():
    ctx = _FakeContext()
    dispatch = anthropic.make_tool_dispatcher(ctx, "execute_trade", contract_id="C1")
    out = dispatch({"args": [1, 2]})
    assert ctx.calls == [("C1", "execute_trade", [1, 2], False)]
    assert "42" in out


# ── gemini (function builder needs no extra) ─────────────────────────────────
def test_gemini_function_invokes_contract():
    ctx = _FakeContext()
    fn = gemini.make_contract_function(ctx, "execute_trade", "C2")
    assert fn.__name__ == "execute_trade"
    out = fn([7])
    assert ctx.calls[0][:3] == ("C2", "execute_trade", [7])
    assert "42" in out


# ── langgraph (import guard) ─────────────────────────────────────────────────
@pytest.mark.skipif(
    importlib.util.find_spec("langchain_core") is None,
    reason="langchain-core not installed",
)
def test_langgraph_tool_builds_when_available():
    tool = langgraph.make_contract_tool(_FakeContext(), "execute_trade", contract_id="C3")
    assert tool is not None


def test_langgraph_guard_errors_without_extra():
    if importlib.util.find_spec("langchain_core") is not None:
        pytest.skip("langchain-core is installed; guard not exercised")
    with pytest.raises(ImportError):
        langgraph.make_contract_tool(_FakeContext(), "execute_trade", contract_id="C3")
