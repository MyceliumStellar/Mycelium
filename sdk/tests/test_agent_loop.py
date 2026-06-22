"""Offline tests for run_agent_loop (fake provider client, no network/key)."""

from types import SimpleNamespace

import pytest

from mycelium_sdk import agent_loop
from mycelium_sdk.adapters import anthropic as anthropic_adapter


class _FakeContext:
    """Stands in for AgentContext — records contract calls, returns canned values."""

    def __init__(self):
        self.calls = []

    def call_contract(self, contract_id, function_name, args, read_only=False):
        self.calls.append((contract_id, function_name, list(args), read_only))
        return SimpleNamespace(return_value=42)


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(name, tool_id, tool_input):
    return SimpleNamespace(type="tool_use", name=name, id=tool_id, input=tool_input)


class _FakeMessages:
    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self._scripted.pop(0)


class _FakeClient:
    def __init__(self, scripted):
        self.messages = _FakeMessages(scripted)


def _patch_anthropic(monkeypatch, scripted):
    """Make require_anthropic() yield a module whose Anthropic() is our fake."""
    client = _FakeClient(scripted)
    fake_module = SimpleNamespace(Anthropic=lambda *a, **k: client)
    monkeypatch.setattr(anthropic_adapter, "require_anthropic", lambda: fake_module)
    return client


def test_anthropic_loop_dispatches_tool_then_returns_text(monkeypatch):
    # Turn 1: model calls increment(); Turn 2: model answers.
    scripted = [
        SimpleNamespace(
            stop_reason="tool_use",
            content=[
                _text_block("calling the tool"),
                _tool_use_block("increment", "toolu_1", {"args": []}),
            ],
        ),
        SimpleNamespace(stop_reason="end_turn", content=[_text_block("The counter is 42.")]),
    ]
    client = _patch_anthropic(monkeypatch, scripted)
    ctx = _FakeContext()

    answer = agent_loop.run_agent_loop(
        "Increment then report.",
        context=ctx,
        provider="anthropic",
        contract_id="CXYZ",
        tools=[agent_loop.ContractTool("increment")],
    )

    assert answer == "The counter is 42."
    # The tool was actually dispatched to the contract.
    assert ctx.calls == [("CXYZ", "increment", [], False)]
    # Second request carried the assistant turn + a single tool_result user turn.
    second = client.messages.requests[1]["messages"]
    assert second[-1]["role"] == "user"
    assert second[-1]["content"][0]["type"] == "tool_result"
    assert second[-1]["content"][0]["tool_use_id"] == "toolu_1"


def test_anthropic_loop_handles_refusal(monkeypatch):
    scripted = [SimpleNamespace(stop_reason="refusal", content=[])]
    _patch_anthropic(monkeypatch, scripted)
    answer = agent_loop.run_agent_loop("do something", context=_FakeContext(), provider="anthropic")
    assert "refused" in answer.lower()


def test_anthropic_loop_passes_system_and_no_tools(monkeypatch):
    scripted = [SimpleNamespace(stop_reason="end_turn", content=[_text_block("hi")])]
    client = _patch_anthropic(monkeypatch, scripted)
    answer = agent_loop.run_agent_loop(
        "hello", context=_FakeContext(), provider="anthropic", system="be terse"
    )
    assert answer == "hi"
    req = client.messages.requests[0]
    assert req["system"] == "be terse"
    assert "tools" not in req  # no tools configured -> omitted


def test_read_only_tool_marks_read_only(monkeypatch):
    scripted = [
        SimpleNamespace(
            stop_reason="tool_use",
            content=[_tool_use_block("get_count", "toolu_9", {"args": []})],
        ),
        SimpleNamespace(stop_reason="end_turn", content=[_text_block("42")]),
    ]
    _patch_anthropic(monkeypatch, scripted)
    ctx = _FakeContext()
    agent_loop.run_agent_loop(
        "read it",
        context=ctx,
        provider="anthropic",
        contract_id="CABC",
        tools=[agent_loop.ContractTool("get_count", read_only=True)],
    )
    assert ctx.calls == [("CABC", "get_count", [], True)]


def test_unsupported_provider_raises():
    with pytest.raises(ValueError, match="Unsupported provider"):
        agent_loop.run_agent_loop("x", context=_FakeContext(), provider="openai")
