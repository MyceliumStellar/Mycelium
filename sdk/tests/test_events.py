"""Offline tests for the shared event-scan utilities (no network)."""

from mycelium_sdk import events as ev


class _Event:
    def __init__(self, eid, ledger):
        self.id = eid
        self.ledger = ledger
        self.topic = []
        self.value = ""


class _Page:
    def __init__(self, events, oldest=None):
        self.events = events
        self.oldest_ledger = oldest


class _Rpc:
    """Returns two full pages then a short page, to exercise cursor pagination."""

    def __init__(self, pages, latest=200):
        self._pages = list(pages)
        self._latest = latest
        self.calls = []

    def get_latest_ledger(self):
        return type("L", (), {"sequence": self._latest})()

    def get_events(self, **kwargs):
        self.calls.append(kwargs)
        if self._pages:
            return self._pages.pop(0)
        return _Page([])


def test_scan_paginates_by_cursor_within_window():
    page_full = _Page([_Event(f"c{i}", 150) for i in range(100)])
    page_tail = _Page([_Event("c100", 151)])
    rpc = _Rpc([page_full, page_tail])
    got = list(
        ev.scan_contract_events(rpc, ["CABC"], start_ledger=100, page_limit=100, retry=None)
    )
    assert len(got) == 101
    # second call must continue by cursor (the last event id of the full page)
    assert rpc.calls[1].get("cursor") == "c99"
    assert "start_ledger" not in rpc.calls[1]


def test_scan_probes_oldest_when_no_start_ledger():
    rpc = _Rpc([_Page([], oldest=42), _Page([_Event("c0", 50)])])
    got = list(ev.scan_contract_events(rpc, ["CABC"], page_limit=100, retry=None))
    # first call is the probe (limit=1); scan then starts at the probed oldest=42
    assert rpc.calls[0]["limit"] == 1
    assert rpc.calls[1]["start_ledger"] == 42
    assert [e.id for e in got] == ["c0"]


def test_parse_registration_event_roundtrip(monkeypatch):
    class _SCVal:
        @staticmethod
        def to_native(x):
            return x

    class _XdrSCVal:
        @staticmethod
        def from_xdr(x):
            return x

    class _Xdr:
        SCVal = _XdrSCVal

    e = _Event("c0", 150)
    e.topic = ["agent_registered"]
    e.value = ["alice", "GABC"]
    parsed = ev.parse_registration_event(e, _SCVal, _Xdr)
    assert parsed == {"name": "alice", "public_key": "GABC", "ledger": 150}

    e.topic = ["something_else"]
    assert ev.parse_registration_event(e, _SCVal, _Xdr) is None
