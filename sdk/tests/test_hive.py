"""Offline tests for HiveClient (return-value parsing, capability hashing)."""

import hashlib

import pytest

from mycelium_sdk import HiveClient


class _FakeContext:
    """Stands in for AgentContext.call_contract in offline tests."""

    def __init__(self, return_value):
        self._return_value = return_value
        self.calls = []

    def call_contract(self, **kwargs):
        self.calls.append(kwargs)
        return self._return_value


def test_capability_hash_is_order_independent():
    a = HiveClient._compute_capability_hash(["b", "a", "c"])
    b = HiveClient._compute_capability_hash(["c", "a", "b"])
    assert a == b
    assert a == hashlib.sha256(b"a,b,c").digest()
    assert len(a) == 32


def test_resolve_parses_dict_return():
    ctx = _FakeContext(
        {"address": "G" + "A" * 55, "capability": b"\x01" * 32,
         "endpoint": b"https://x.sh/api", "reputation": 9}
    )
    meta = HiveClient(ctx).resolve_agent("foo")
    assert meta["public_key"].startswith("G")
    assert meta["endpoint"] == "https://x.sh/api"  # bytes decoded
    assert meta["reputation"] == 9


def test_resolve_parses_positional_return():
    ctx = _FakeContext(["G" + "B" * 55, b"\x02" * 32, b"https://y.sh", 3])
    meta = HiveClient(ctx).resolve_agent("bar")
    assert meta["endpoint"] == "https://y.sh"
    assert meta["reputation"] == 3


def test_register_sends_bytes_endpoint():
    ctx = _FakeContext(object())
    hc = HiveClient(ctx)
    hc.context.keypair = type("KP", (), {"public_key": "G" + "C" * 55})()
    hc.register("alice", ["x"], "https://a.sh")
    args = ctx.calls[0]["args"]
    assert args[0] == "alice"
    assert isinstance(args[3], bytes) and args[3] == b"https://a.sh"


def test_resolve_unregistered_raises():
    with pytest.raises(KeyError):
        HiveClient(_FakeContext(None)).resolve_agent("missing")


# ── discovery (event-scan) ───────────────────────────────────────────────────

class _FakeEvent:
    def __init__(self, name, address, ledger, event_id):
        from stellar_sdk import scval
        self.topic = [scval.to_symbol("agent_registered").to_xdr()]
        self.value = scval.to_vec([scval.to_symbol(name), scval.to_address(address)]).to_xdr()
        self.ledger = ledger
        self.id = event_id


class _FakePage:
    def __init__(self, events, oldest=100, latest=200, cursor="c"):
        self.events = events
        self.oldest_ledger = oldest
        self.latest_ledger = latest
        self.cursor = cursor


class _FakeRpc:
    """Minimal SorobanServer stand-in returning one window of events."""

    def __init__(self, events):
        self._events = events
        self.windows = 0

    def get_latest_ledger(self):
        return type("L", (), {"sequence": 200})()

    def get_events(self, **kwargs):
        # First call per window returns the events; nothing beyond.
        if kwargs.get("cursor") is None and self.windows == 0:
            self.windows += 1
            return _FakePage(self._events)
        return _FakePage([])


class _DiscoverContext:
    """Context exposing soroban_rpc plus a resolve_agent-shaped call_contract."""

    def __init__(self, events, resolved):
        self.soroban_rpc = _FakeRpc(events)
        self._resolved = resolved

    def call_contract(self, **kwargs):
        return self._resolved


def _random_address():
    from stellar_sdk import Keypair
    return Keypair.random().public_key


def test_discover_scans_events_without_resolution():
    addr_a = _random_address()
    addr_b = _random_address()
    events = [_FakeEvent("alice", addr_a, 150, "c1"), _FakeEvent("bob", addr_b, 160, "c2")]
    hc = HiveClient(_DiscoverContext(events, resolved=None))
    agents = hc.discover_agents(start_ledger=100, resolve=False)
    names = {a["name"] for a in agents}
    assert names == {"alice", "bob"}
    assert all(a["public_key"].startswith("G") for a in agents)


def test_discover_resolves_each_agent():
    addr = _random_address()
    events = [_FakeEvent("alice", addr, 150, "c1")]
    resolved = {"address": addr, "capability": b"\x01" * 32, "endpoint": b"https://a.sh", "reputation": 7}
    hc = HiveClient(_DiscoverContext(events, resolved=resolved))
    agents = hc.discover_agents(start_ledger=100, resolve=True)
    assert len(agents) == 1
    assert agents[0]["name"] == "alice"
    assert agents[0]["endpoint"] == "https://a.sh"
    assert agents[0]["reputation"] == 7
