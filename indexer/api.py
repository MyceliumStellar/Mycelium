"""
Indexer read API — O(1) agent/job discovery over the Firestore cache.

Hosted-first: the SDK/CLI/IDE point at this service's URL and fall back to the
on-chain event-scan when it's unreachable. Every response carries
`source_contract` + `as_of_ledger` so a client can re-verify any row on-chain
(DB speed, chain trust).

The route handlers depend on a `Store` (see `store.py`) resolved via
`get_store`; tests override that dependency with an in-memory fake. Mount on the
existing IDE backend app or run standalone:

    uvicorn indexer.api:app --port 8080
"""

import hashlib
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from mycelium_sdk.constants import HIVEMIND_REGISTRY_ADDRESS

app = FastAPI(title="Mycelium Indexer", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _maybe_start_worker() -> None:
    """
    Optionally run the ingest worker in a background daemon thread so a single
    web service both serves reads and keeps Firestore fresh. Enable with
    RUN_INDEXER_WORKER=1; otherwise this is a read-only API (run the worker
    separately via `python -m indexer.worker`).
    """
    import os

    if os.getenv("RUN_INDEXER_WORKER", "").lower() not in ("1", "true", "yes"):
        return

    import threading

    def _run():
        from indexer.worker import build_default_worker

        network = os.getenv("MYCELIUM_NETWORK", "testnet")
        poll = int(os.getenv("INDEXER_POLL_SECONDS", "10"))
        print(f"[indexer] background worker starting (network={network}, poll={poll}s)")
        build_default_worker(network).run_forever(poll_interval=poll)

    threading.Thread(target=_run, name="indexer-worker", daemon=True).start()


def get_store():
    """Production dependency: a Firestore-backed store. Overridden in tests."""
    from indexer.firestore_client import get_firestore
    from indexer.store import FirestoreStore

    return FirestoreStore(get_firestore())


def get_capability_verifier():
    """
    Production dependency: returns `name -> on-chain capability_hash (bytes)` by
    resolving the agent on-chain, so submitted plaintext tags can be verified
    against the chain. Overridden in tests.
    """
    def _verify(name: str) -> Optional[bytes]:
        from mycelium_sdk import AgentContext, HiveClient

        entry = HiveClient(AgentContext.read_only()).resolve_agent(name)
        h = entry.get("capability_hash")
        return bytes(h) if h is not None else None

    return _verify


def _capability_hash(tags: List[str]) -> bytes:
    """Must match HiveClient._compute_capability_hash (sorted, comma-joined)."""
    return hashlib.sha256(",".join(sorted(tags)).encode("utf-8")).digest()


def _board_address() -> Optional[str]:
    import os

    env = os.getenv("MYCELIUM_BOARD_ADDRESS")
    if env:
        return env
    try:
        from mycelium_cli.config import get_value

        return get_value("jobs", "board_address")
    except Exception:
        return None


def _envelope(store, source_contract, **payload):
    return {
        "source_contract": source_contract,
        "as_of_ledger": store.as_of_ledger(),
        **payload,
    }


@app.get("/agents")
def list_agents(
    capability: Optional[str] = None,
    min_reputation: int = 0,
    limit: int = Query(50, ge=1, le=200),
    start_after: Optional[str] = None,
    store=Depends(get_store),
):
    rows, next_cursor = store.list_agents(capability, min_reputation, limit, start_after)
    return _envelope(store, HIVEMIND_REGISTRY_ADDRESS, agents=rows, next_cursor=next_cursor)


@app.get("/agents/{name}")
def get_agent(name: str, store=Depends(get_store)):
    agent = store.get_agent(name)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not indexed.")
    return _envelope(store, HIVEMIND_REGISTRY_ADDRESS, agent=agent)


@app.get("/jobs")
def list_jobs(
    status: Optional[str] = None,
    mode: Optional[str] = None,
    min_bounty: int = 0,
    limit: int = Query(50, ge=1, le=200),
    start_after: Optional[str] = None,
    store=Depends(get_store),
):
    rows, next_cursor = store.list_jobs(status, mode, min_bounty, limit, start_after)
    return _envelope(store, _board_address(), jobs=rows, next_cursor=next_cursor)


@app.get("/jobs/{job_id}")
def get_job(job_id: str, store=Depends(get_store)):
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not indexed.")
    return _envelope(store, _board_address(), job=job)


class CapabilitiesIn(BaseModel):
    tags: List[str]


@app.post("/agents/{name}/capabilities")
def publish_capabilities(
    name: str,
    body: CapabilitiesIn,
    store=Depends(get_store),
    verify=Depends(get_capability_verifier),
):
    """
    Record plaintext capability tags for an agent so capability search works.

    Trustless: the tags are accepted only if their hash matches the agent's
    on-chain `capability_hash`, so a third party cannot inject false tags.
    """
    if not body.tags:
        raise HTTPException(status_code=400, detail="tags must be non-empty.")
    try:
        onchain = verify(name)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not on-chain: {e}")
    if onchain is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' has no capability hash.")
    if _capability_hash(body.tags) != onchain:
        raise HTTPException(status_code=400, detail="tags do not match on-chain capability hash.")
    tags = sorted(body.tags)
    store.set_capability_tags(name, tags)
    return {"ok": True, "name": name, "capability_tags": tags}


@app.get("/stats")
def stats(store=Depends(get_store)):
    return _envelope(store, None, stats=store.stats())


@app.get("/healthz")
def healthz():
    return {"ok": True}
