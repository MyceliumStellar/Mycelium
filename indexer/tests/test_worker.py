"""Offline tests for the indexer worker + event parsing (in-memory Firestore fake)."""

import pytest

from indexer import parsing
from indexer.worker import IndexerWorker


# ── identity scval/xdr (events already carry native python in these fakes) ──────
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


@pytest.fixture(autouse=True)
def _patch_stellar(monkeypatch):
    """worker.run_once imports scval/xdr from stellar_sdk; swap in identities."""
    import stellar_sdk

    monkeypatch.setattr(stellar_sdk, "scval", _SCVal, raising=False)
    monkeypatch.setattr(stellar_sdk, "xdr", _Xdr, raising=False)


# ── in-memory firestore fake ───────────────────────────────────────────────────
class _Snap:
    def __init__(self, data):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data) if self._data else {}


class _Doc:
    def __init__(self, store, path):
        self._store = store
        self._path = path

    def set(self, data, merge=False):
        if merge and self._path in self._store:
            self._store[self._path] = {**self._store[self._path], **data}
        else:
            self._store[self._path] = dict(data)

    def get(self):
        return _Snap(self._store.get(self._path))

    def collection(self, name):
        return _Collection(self._store, f"{self._path}/{name}")


class _Collection:
    def __init__(self, store, prefix):
        self._store = store
        self._prefix = prefix

    def document(self, doc_id):
        return _Doc(self._store, f"{self._prefix}/{doc_id}")


class _FakeDb:
    def __init__(self):
        self.store = {}

    def collection(self, name):
        return _Collection(self.store, name)


# ── event + rpc fakes ──────────────────────────────────────────────────────────
class _Event:
    def __init__(self, topic, value, ledger, eid, contract_id="CBOARD"):
        self.topic = [topic]
        self.value = value
        self.ledger = ledger
        self.id = eid
        self.contract_id = contract_id


class _Page:
    def __init__(self, events, oldest=None):
        self.events = events
        self.oldest_ledger = oldest


class _Rpc:
    def __init__(self, events, latest=500):
        self._events = events
        self._latest = latest
        self._served = False

    def get_latest_ledger(self):
        return type("L", (), {"sequence": self._latest})()

    def get_events(self, **kwargs):
        if not self._served:
            self._served = True
            return _Page(self._events)
        return _Page([])


def _worker(events, resolve=None, resolve_job=None):
    db = _FakeDb()
    rpc = _Rpc(events)
    w = IndexerWorker(
        db, rpc, {"registry": "CREG", "job_board": "CBOARD"},
        resolve_agent=resolve, resolve_job=resolve_job,
    )
    return w, db


# ── tests ───────────────────────────────────────────────────────────────────--
def test_agent_registration_upserts_with_resolution():
    events = [_Event("agent_registered", ["alice", "GALICE"], 100, "100-0", "CREG")]
    resolved = {"endpoint": "https://a.sh", "reputation": 7, "capability_tags": ["vision", "nlp"]}
    w, db = _worker(events, resolve=lambda name: resolved)
    counts = w.run_once(from_ledger=1)
    assert counts == {"agent": 1}
    doc = db.store["agents/alice"]
    assert doc["address"] == "GALICE"
    assert doc["endpoint"] == "https://a.sh"
    assert doc["capability_tags"] == ["vision", "nlp"]
    assert doc["first_seen_ledger"] == 100


def test_job_lifecycle_advances_status():
    events = [
        _Event("job_posted", [1, "GPOSTER", 5_000_000], 101, "101-0"),
        _Event("job_claimed", [1, "GAGENT"], 102, "102-0"),
        _Event("job_submitted", [1], 103, "103-0"),
        _Event("job_completed", [1], 104, "104-0"),
    ]
    w, db = _worker(events)
    counts = w.run_once(from_ledger=1)
    assert counts["job_posted"] == 1 and counts["job_status"] == 3
    job = db.store["jobs/1"]
    assert job["poster"] == "GPOSTER"
    assert job["bounty"] == 5_000_000
    assert job["agent"] == "GAGENT"
    assert job["status"] == "done"


def test_job_posted_enriched_with_full_details():
    events = [_Event("job_posted", [3, "GPOSTER", 7_000_000], 120, "120-0")]
    calls = []

    def resolve_job(job_id):
        calls.append(job_id)
        return {"token": "CTOKEN", "mode": "single", "escrow": "CESCROW", "deadline": 999}

    w, db = _worker(events, resolve_job=resolve_job)
    w.run_once(from_ledger=1)
    job = db.store["jobs/3"]
    assert calls == [3]                       # resolved once
    assert job["escrow"] == "CESCROW"
    assert job["deadline"] == 999
    assert job["token"] == "CTOKEN"
    assert job["mode"] == "single"
    assert job["bounty"] == 7_000_000         # from the event


def test_swarm_join_writes_member_and_share():
    events = [
        _Event("job_posted", [2, "GPOSTER", 9_000_000], 110, "110-0"),
        _Event("swarm_joined", [2, "GMEM1", 6000], 111, "111-0"),
        _Event("swarm_joined", [2, "GMEM2", 4000], 112, "112-0"),
    ]
    w, db = _worker(events)
    w.run_once(from_ledger=1)
    assert db.store["jobs/2"]["mode"] == "swarm"
    assert db.store["jobs/2/members/GMEM1"]["share_bps"] == 6000
    assert db.store["jobs/2/members/GMEM2"]["share_bps"] == 4000


def test_memory_anchor_latest_version_per_owner():
    events = [
        _Event("memory_anchored", ["GOWNER", 1], 130, "130-0", "CANCHOR"),
        _Event("memory_anchored", ["GOWNER", 2], 131, "131-0", "CANCHOR"),
    ]
    w, db = _worker(events)
    counts = w.run_once(from_ledger=1)
    assert counts["memory_anchor"] == 2
    doc = db.store["memory_anchors/GOWNER"]
    assert doc["version"] == 2                     # latest wins
    assert doc["last_anchor_ledger"] == 131


def test_settlement_recorded_by_event_id():
    events = [
        _Event("escrow_split", [2, 9_000_000], 113, "113-0", "CESCROW"),
    ]
    w, db = _worker(events)
    counts = w.run_once(from_ledger=1)
    assert counts["settlement"] == 1
    s = db.store["settlements/113-0"]
    assert s["kind"] == "split"
    assert s["amount"] == 9_000_000
    assert s["count"] == 2


def test_reingest_is_idempotent():
    events = [
        _Event("job_posted", [1, "GPOSTER", 5_000_000], 101, "101-0"),
        _Event("escrow_locked", ["GPROVIDER", 5_000_000], 101, "101-1", "CESCROW"),
    ]
    w, db = _worker(events)
    w.run_once(from_ledger=1)
    snapshot = dict(db.store)
    # Re-run the same events from the same ledger: docs overwrite, no dupes.
    w._rpc = _Rpc(events)
    w.rpc = _Rpc(events)
    w.run_once(from_ledger=1)
    assert set(db.store.keys()) == set(snapshot.keys())


def test_cursor_advances_to_last_event_ledger():
    events = [_Event("job_posted", [1, "GP", 1], 207, "207-0")]
    w, db = _worker(events)
    w.run_once(from_ledger=1)
    cur = db.store["indexer_meta/cursor"]
    assert cur["last_ledger"] == 207
    assert cur["last_event_id"] == "207-0"
