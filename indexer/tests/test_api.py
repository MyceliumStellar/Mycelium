"""Offline tests for the indexer read API (fake store via dependency override)."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from indexer.api import app, get_store, get_capability_verifier, _capability_hash


class _FakeStore:
    def __init__(self):
        self._agents = {
            "alice": {"name": "alice", "address": "GALICE", "reputation": 9,
                      "capability_tags": ["vision"], "endpoint": "https://a.sh"},
            "bob": {"name": "bob", "address": "GBOB", "reputation": 3,
                    "capability_tags": ["nlp"]},
        }
        self._jobs = {
            "1": {"job_id": "1", "status": "open", "bounty": 5_000_000, "mode": "single"},
        }

    def as_of_ledger(self):
        return 12345

    def list_agents(self, capability=None, min_reputation=0, limit=50, start_after=None):
        rows = [a for a in self._agents.values()
                if (capability is None or capability in a["capability_tags"])
                and a["reputation"] >= min_reputation]
        return rows, None

    def get_agent(self, name):
        return self._agents.get(name)

    def list_jobs(self, status=None, mode=None, min_bounty=0, limit=50, start_after=None):
        rows = [j for j in self._jobs.values()
                if (status is None or j["status"] == status)
                and j["bounty"] >= min_bounty]
        return rows, None

    def get_job(self, job_id):
        return self._jobs.get(str(job_id))

    def set_capability_tags(self, name, tags):
        self._agents.setdefault(name, {"name": name})["capability_tags"] = tags

    def stats(self):
        return {"agents": 2, "jobs": 1, "settlements": 0, "volume_stroops": 0}


@pytest.fixture
def client():
    app.dependency_overrides[get_store] = lambda: _FakeStore()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_list_agents_envelope_and_filter(client):
    r = client.get("/agents?capability=vision")
    assert r.status_code == 200
    body = r.json()
    assert body["as_of_ledger"] == 12345
    assert body["source_contract"]  # registry address present
    assert [a["name"] for a in body["agents"]] == ["alice"]


def test_min_reputation_filter(client):
    r = client.get("/agents?min_reputation=5")
    assert [a["name"] for a in r.json()["agents"]] == ["alice"]


def test_get_agent_404(client):
    assert client.get("/agents/nobody").status_code == 404
    assert client.get("/agents/alice").json()["agent"]["address"] == "GALICE"


def test_list_and_get_jobs(client):
    assert client.get("/jobs?status=open").json()["jobs"][0]["job_id"] == "1"
    assert client.get("/jobs?status=done").json()["jobs"] == []
    assert client.get("/jobs/1").json()["job"]["bounty"] == 5_000_000
    assert client.get("/jobs/999").status_code == 404


def test_stats(client):
    assert client.get("/stats").json()["stats"]["agents"] == 2


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_publish_capabilities_verifies_against_onchain_hash():
    store = _FakeStore()
    tags = ["nlp", "vision"]
    onchain_hash = _capability_hash(tags)
    app.dependency_overrides[get_store] = lambda: store
    # verifier returns the matching on-chain hash for alice, mismatch for others
    app.dependency_overrides[get_capability_verifier] = lambda: (
        lambda name: onchain_hash if name == "alice" else b"\x00" * 32
    )
    c = TestClient(app)
    # matching tags accepted + stored (sorted)
    r = c.post("/agents/alice/capabilities", json={"tags": ["vision", "nlp"]})
    assert r.status_code == 200
    assert store._agents["alice"]["capability_tags"] == ["nlp", "vision"]
    # tags that don't hash to the on-chain value are rejected
    assert c.post("/agents/bob/capabilities", json={"tags": ["nlp", "vision"]}).status_code == 400
    # empty tags rejected
    assert c.post("/agents/alice/capabilities", json={"tags": []}).status_code == 400
    app.dependency_overrides.clear()
