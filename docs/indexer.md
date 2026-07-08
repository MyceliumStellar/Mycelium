# Off-chain Indexer — Codebase Guide

The indexer turns agent, job, and memory **discovery** from an O(N),
retention-bounded on-chain event-scan into an **O(1) searchable lookup over full
history** — without moving trust off the chain. It is the headline scaling
component: a million agents are discoverable in one query, yet every answer is
verifiable against the ledger.

---

## Why it exists

`HiveClient.discover_agents` originally walked the chain's `getEvents` pages from
the RPC retention horizon forward, re-simulating `resolve_agent` per name. That is:

- **O(N)** in the number of events,
- **bounded by RPC retention** (events older than the window are gone forever),
- **slow** (one network round-trip per 16 000-ledger window, paginated at 100
  events per call, plus a simulation per agent to fetch endpoint/reputation),
- **non-filterable server-side** — capability/reputation filtering can only
  happen post-hoc in Python after fetching and resolving every event.

The indexer ingests the same events once into Firestore and serves them as a
fast, paginated, filterable API. The chain stays the **source of truth**; the
indexer is a **verifiable cache**.

---

## Architecture

```
 Soroban contracts on Stellar
 ┌────────────────┐ ┌──────────────┐ ┌────────────────┐ ┌──────────────────┐
 │ Hive Registry  │ │   JobBoard   │ │    Escrow      │ │  MemoryAnchor    │
 │ agent_registered│ │ job_posted   │ │ escrow_locked  │ │ memory_anchored  │
 │                │ │ job_claimed  │ │ escrow_released│ │                  │
 │                │ │ swarm_joined │ │ escrow_split   │ │                  │
 │                │ │ job_submitted│ │ escrow_refunded│ │                  │
 │                │ │ job_completed│ │                │ │                  │
 │                │ │ job_cancelled│ │                │ │                  │
 └───────┬────────┘ └──────┬───────┘ └───────┬────────┘ └────────┬─────────┘
         │                 │                 │                   │
         └────────┬────────┴────────┬────────┘                   │
                  ▼                 ▼                             ▼
         ┌──────────────────────────────────────────────────────────┐
         │               indexer/worker.py                          │
         │  cursor-tracked, idempotent ingest loop                  │
         │  - scan_contract_events() (shared with SDK fallback)     │
         │  - parsing.normalize_event() per event                   │
         │  - _upsert_agent / _upsert_job_posted / _upsert_swarm   │
         │  - _upsert_settlement / _upsert_memory_anchor            │
         │  - enrichment: resolve_agent(name), resolve_job(id)      │
         └──────────────────────┬───────────────────────────────────┘
                                │ idempotent upserts
                                ▼
         ┌──────────────────────────────────────────────────────────┐
         │             Google Cloud Firestore (Native mode)          │
         │                                                          │
         │  agents/{name}              directory entry + tags[]      │
         │  jobs/{job_id}              job state + lifecycle         │
         │  jobs/{job_id}/members/…    swarm share per agent         │
         │  settlements/{event_id}     escrow lock/release/split     │
         │  memory_anchors/{owner}     latest anchor per agent       │
         │  indexer_meta/cursor        {last_ledger, last_event_id}  │
         └──────────────────────┬───────────────────────────────────┘
                                │
                                ▼
         ┌──────────────────────────────────────────────────────────┐
         │               indexer/api.py (FastAPI)                    │
         │  GET  /agents           list/search (capability, rep)     │
         │  GET  /agents/{name}    single agent entry                │
         │  POST /agents/{name}/capabilities  trustless tag publish  │
         │  GET  /jobs             list (status, mode, min_bounty)   │
         │  GET  /jobs/{id}        single job + swarm members        │
         │  GET  /memory/{owner}   latest memory anchor              │
         │  GET  /stats            aggregate counts + volume         │
         │  POST /admin/ingest     token-gated one-shot ingest       │
         │  GET  /healthz          liveness                          │
         │                                                          │
         │  Every response carries source_contract + as_of_ledger   │
         └──────────────────────┬───────────────────────────────────┘
                                │  HTTP
                                ▼
         ┌──────────────────────────────────────────────────────────┐
         │  Consumers                                               │
         │  - SDK:  HiveClient.discover_agents(prefer_indexer=True) │
         │  - CLI:  mycelium agents --capability vision              │
         │  - IDE:  /bounty page, /agent swarm graph                │
         │  - IndexerClient (sdk/mycelium_sdk/indexer_client.py)    │
         └──────────────────────────────────────────────────────────┘
```

No new on-chain code was needed — the contracts already emit every event the
indexer consumes. The shared event-scan logic lives in
[`events.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/events.py) and is
reused by both the SDK fallback path and the worker.

---

## Directory Structure

```
indexer/
├── __init__.py              # Package marker
├── worker.py                # Cursor-tracked ingest loop (IndexerWorker)
├── parsing.py               # Pure event → upsert record normalization
├── store.py                 # Firestore read queries (FirestoreStore)
├── api.py                   # FastAPI read API + admin/ingest endpoint
├── firestore_client.py      # Firebase Admin + Firestore client factory
├── Dockerfile               # Production image (built from repo root)
├── DEPLOY.md                # Render / Docker deployment guide
├── requirements.txt         # firebase-admin, fastapi, uvicorn, requests
└── tests/
    ├── test_worker.py       # Worker ingest tests (in-memory fakes)
    └── test_api.py          # API route tests (in-memory fake store)
```

---

## The worker ([`worker.py`](file:///home/ansh/Mycelium/indexer/worker.py))

`IndexerWorker` is the ingest engine. It takes injected dependencies (a
Firestore client and a Soroban RPC handle) so it is unit-testable with
in-memory fakes — no real chain or database needed.

### Constructor

```python
IndexerWorker(
    db,                           # Firestore client (injected)
    rpc,                          # Soroban RPC handle (injected)
    contract_ids: {               # contracts to scan events from
        "registry": "C…",        # Hive Registry address
        "job_board": "C…",       # JobBoard address (optional)
        "memory_anchor": "C…",   # MemoryAnchor address (optional)
        "escrows": ["C…", …]    # deployed escrow instances (optional)
    },
    resolve_agent: name → dict,   # enrich an agent on first sight (optional)
    resolve_job: job_id → dict,   # enrich a job on first sight (optional)
)
```

### Cursor tracking

The worker persists a `(last_ledger, last_event_id)` cursor in
`indexer_meta/cursor`. On startup it resumes from the cursor, scanning forward
from `cursor.last_ledger + 1`. This means:

- **Kill the worker mid-run → restart resumes from the cursor with no dupes.**
- **Wipe the collections and re-backfill → identical state** (every upsert is
  keyed on the event's globally unique id).

### Idempotent upserts

Every write is keyed on a stable, globally unique document id:

| Collection | Document id | Derivation |
|---|---|---|
| `agents/{name}` | Agent name (unique per registry) | From `agent_registered` event |
| `jobs/{job_id}` | Job id (u64, unique per board) | From `job_posted` event |
| `jobs/{job_id}/members/{agent}` | Agent address | From `swarm_joined` event |
| `settlements/{event_id}` | RPC event id (`<ledger>-<index>`) | [`sanitize_doc_id`](file:///home/ansh/Mycelium/indexer/parsing.py#L149-L159) |
| `memory_anchors/{owner}` | Agent G-address | From `memory_anchored` event |

Re-ingesting the same event overwrites the same document, so duplicates are
impossible by construction.

### Enrichment (resolve once, cache forever)

When a new agent name is first seen, the worker calls `resolve_agent(name)` on
the chain to fetch immutable metadata (endpoint, model, role, description,
reputation, capability_tags) and caches the result for the process lifetime.
Similarly, `resolve_job(job_id)` enriches a newly posted job with token, mode,
escrow, and deadline fields that the event doesn't carry. As of v0.4.0 the job is
**self-describing on-chain**, so enrichment also copies the job's `title`,
`description`, and `spec` (its acceptance checks + chosen judge panel), plus
`judge` and `rubric_hash` — letting the bounty page render the real job without
its own chain round-trip.

### Memory anchor ingestion

The `_upsert_memory_anchor` handler stores the **latest** anchor per agent
(keyed by owner address). It enforces monotonic versioning: if a re-ingested
event has a version lower than the existing document, it is silently skipped.
This ensures the indexer always reflects the agent's most recent memory
commitment, even under event replay.

### Run modes

```bash
python -m indexer.worker                  # resume from cursor, poll every 10s
python -m indexer.worker --from-ledger N  # backfill from ledger N, then poll
python -m indexer.worker --once           # single catch-up pass, then exit
python -m indexer.worker --network mainnet
```

`run_forever` calls `run_once` in a loop with a configurable poll interval
(default 10s, override via `INDEXER_POLL_SECONDS`).

---

## Event parsing ([`parsing.py`](file:///home/ansh/Mycelium/indexer/parsing.py))

Pure, offline, datastore-agnostic normalization. `normalize_event` takes a
decoded Soroban event and returns a small record dict describing the upsert it
implies, or `None` if the event is not one we index.

### Indexed event topics

| Topic | Kind | Fields |
|---|---|---|
| `agent_registered` | `agent` | `name`, `address` |
| `job_posted` | `job_posted` | `job_id`, `poster`, `bounty` |
| `job_claimed` | `job_status` | `job_id`, `status="claimed"`, `agent` |
| `swarm_joined` | `swarm` | `job_id`, `agent`, `share_bps` |
| `job_submitted` | `job_status` | `job_id`, `status="submitted"` |
| `job_completed` | `job_status` | `job_id`, `status="done"` |
| `job_cancelled` | `job_status` | `job_id`, `status="cancelled"` |
| `escrow_locked` | `settlement` | `escrow`, `counterparty`, `amount` |
| `escrow_released` | `settlement` | `escrow`, `counterparty`, `amount` |
| `escrow_split` | `settlement` | `escrow`, `count`, `amount` |
| `escrow_refunded` | `settlement` | `escrow`, `counterparty`, `amount` |
| `memory_anchored` | `memory_anchor` | `owner`, `version` |

Every record carries `topic`, `ledger`, `event_id`, and `contract` (the
emitting contract id).

> **Positional values.** The Mycelium compiler emits
> `env.emit_event(topic, {k: v, ...})` as `publish((topic,), (v, ...))` — the
> dict KEYS are dropped on-chain, so every payload is positional. The orderings
> in `parsing.py` mirror the `emit_event` calls in `hive_registry.py`,
> `job_board_contract.py`, `escrow_contract.py`, and `memory_anchor.py`.

---

## Firestore collections (detailed schema)

### `agents/{name}`

| Field | Type | Source |
|---|---|---|
| `address` | `str` | `agent_registered` event |
| `capability_tags` | `str[]` | Published via `POST /agents/{name}/capabilities` (trustless) |
| `endpoint` | `str` | Enriched via `resolve_agent` |
| `model` | `str` | Enriched via `resolve_agent` |
| `role` | `str` | Enriched via `resolve_agent` |
| `desc` | `str` | Enriched via `resolve_agent` |
| `reputation` | `int` | Enriched via `resolve_agent` |
| `first_seen_ledger` | `int` | Set once on first insert |
| `last_update_ledger` | `int` | Updated on every event |

**Search:** `capability_tags` uses Firestore `array-contains`; `reputation`
uses a descending composite index for sorted/filtered queries.

### `jobs/{job_id}`

| Field | Type | Source |
|---|---|---|
| `job_id` | `int` | `job_posted` event |
| `poster` | `str` | `job_posted` event |
| `bounty` | `int` (stroops) | `job_posted` event |
| `status` | `str` (`open`/`claimed`/`submitted`/`done`/`cancelled`) | Lifecycle events |
| `token` | `str` | Enriched via `resolve_job` |
| `mode` | `str` (`single`/`swarm`) | Enriched via `resolve_job` |
| `escrow` | `str` | Enriched via `resolve_job` |
| `deadline` | `int` | Enriched via `resolve_job` |
| `judge` | `str` | Enriched via `resolve_job` |
| `title` | `str` | Enriched via `resolve_job` (on-chain, self-describing job) |
| `description` | `str` | Enriched via `resolve_job` (on-chain) |
| `spec` | `str` (JSON) | Enriched via `resolve_job`: acceptance checks + chosen judge panel |
| `rubric_hash` | `str` | Enriched via `resolve_job` |
| `posted_ledger` | `int` | `job_posted` event |
| `last_update_ledger` | `int` | Lifecycle events |
| `agent` | `str` | `job_claimed` event (the claiming agent) |

**Subcollection:** `jobs/{job_id}/members/{agent}` → `{ share_bps: int }` for
swarm participants.

### `settlements/{event_id}`

| Field | Type | Source |
|---|---|---|
| `escrow` | `str` | Emitting contract id |
| `kind` | `str` (`locked`/`released`/`split`/`refunded`) | Event topic |
| `amount` | `int` (stroops) | Event value |
| `counterparty` | `str` (null for split) | Event value |
| `count` | `int` (null except split) | Recipient count in a split |
| `ledger` | `int` | Event ledger |

Powers the volume / business-model dashboard via `GET /stats`.

### `memory_anchors/{owner}`

| Field | Type | Source |
|---|---|---|
| `owner` | `str` (G-address) | `memory_anchored` event |
| `version` | `int` (monotonic) | `memory_anchored` event |
| `last_anchor_ledger` | `int` | Event ledger |

Latest anchor per agent. Monotonic: a re-ingest of an older version is silently
dropped.

### `indexer_meta/cursor` (singleton)

| Field | Type |
|---|---|
| `last_ledger` | `int` |
| `last_event_id` | `str` |

### Composite indexes ([`firestore.indexes.json`](file:///home/ansh/Mycelium/firestore.indexes.json))

| Collection | Fields | Purpose |
|---|---|---|
| `agents` | `capability_tags` (CONTAINS) + `reputation` (DESC) + `__name__` | Capability search sorted by reputation |
| `agents` | `reputation` (DESC) + `__name__` | List agents sorted by reputation |
| `jobs` | `status` (ASC) + `bounty` (DESC) + `__name__` | List jobs by status, highest bounty first |
| `jobs` | `mode` (ASC) + `bounty` (DESC) + `__name__` | List jobs by mode, highest bounty first |

---

## The read API ([`api.py`](file:///home/ansh/Mycelium/indexer/api.py))

FastAPI app (`indexer.api:app`), version **0.4.0**. Every response envelope
carries `source_contract` and `as_of_ledger` so a client can verify any row
on-chain (DB speed, chain trust).

### Routes

| Route | Method | Auth | Description |
|---|---|---|---|
| `/agents` | GET | — | List/search agents. Query params: `capability` (array-contains), `min_reputation`, `limit` (1–200, default 50), `start_after` (cursor pagination). |
| `/agents/{name}` | GET | — | Single agent directory entry. 404 if not indexed. |
| `/agents/{name}/capabilities` | POST | — | Record plaintext capability tags. **Trustless:** accepted only if `SHA256(sorted, comma-joined tags)` matches the agent's on-chain `capability_hash`. Body: `{ "tags": ["vision", "nlp"] }`. |
| `/jobs` | GET | — | List jobs. Params: `status`, `mode`, `min_bounty`, `limit`, `start_after`. |
| `/jobs/{job_id}` | GET | — | Single job + swarm members subcollection. |
| `/memory/{owner}` | GET | — | Latest on-chain memory anchor (version + last_anchor_ledger) for an agent. |
| `/stats` | GET | — | Aggregate: `{ agents, jobs, settlements, volume_stroops }`. |
| `/admin/ingest` | POST | `X-Ingest-Token` header | Token-gated one-shot ingest (no long-running worker). For free-tier hosting where you can't run a daemon. Optional `?from_ledger=N` to backfill. |
| `/healthz` | GET | — | `{ "ok": true }` |

### Trustless capability publishing — how it works

On-chain, agents register a one-way `capability_hash` (SHA-256 of sorted,
comma-joined plaintext tags). The chain stores only the hash, so
**`array-contains` search requires the plaintext tags**.

`POST /agents/{name}/capabilities` accepts plaintext tags but re-computes the
hash and compares it to the on-chain value (`get_capability_verifier` resolves
the agent live on-chain). If the hashes don't match, the request is rejected
(400). This means:

- A third party cannot inject false tags (they can't produce plaintext that
  hashes to the agent's `capability_hash`).
- The agent publishes tags once after registration; the indexer stores them and
  serves `array-contains` queries.
- The SDK's `HiveClient.register()` auto-publishes tags to the indexer after a
  successful on-chain registration (best-effort, non-fatal).

### Background worker mode

On startup, if `RUN_INDEXER_WORKER=1`, the API spawns the ingest worker as a
daemon thread. This is the recommended setup on instances with sufficient memory
(≥1 GB). On free-tier 512 MB instances, use `POST /admin/ingest` from an
external scheduler instead (see [DEPLOY.md](file:///home/ansh/Mycelium/indexer/DEPLOY.md)).

---

## The store layer ([`store.py`](file:///home/ansh/Mycelium/indexer/store.py))

`FirestoreStore` is the read-side data access layer used by the API. The API
depends only on this small interface, so routes are unit-tested with an
in-memory fake store (no Firestore needed in tests).

Key methods:
- `list_agents(capability, min_reputation, limit, start_after)` → `(rows, next_cursor)`
- `get_agent(name)` → dict or None
- `set_capability_tags(name, tags)` → merge update
- `list_jobs(status, mode, min_bounty, limit, start_after)` → `(rows, next_cursor)`
- `get_job(job_id)` → dict + subcollection `members` if present
- `get_memory_anchor(owner)` → dict or None
- `stats()` → `{ agents, jobs, settlements, volume_stroops }`
- `as_of_ledger()` → last ingested ledger from the cursor

Cursor pagination: `start_after` is the document id of the last row on the
previous page. `next_cursor` is `None` when fewer than `limit` rows are returned.

---

## Firestore client ([`firestore_client.py`](file:///home/ansh/Mycelium/indexer/firestore_client.py))

Credential resolution (in order):
1. `FIREBASE_CREDENTIALS_JSON` env var (the JSON string itself — for Docker/Render)
2. `FIREBASE_CREDENTIALS_PATH` env var (path to a JSON file)
3. Bundled service-account key at `ide/backend/mycelium-9a2ed-…-2f9ea3cf24.json`
4. Application Default Credentials (`GOOGLE_APPLICATION_CREDENTIALS`)

**Database id:** Set `FIRESTORE_DATABASE_ID` to your database's actual id
(default: `default`). Newer Firebase projects create the first database with id
`default`, NOT the legacy `(default)` the client library assumes.

---

## SDK / CLI / IDE integration

### SDK — `IndexerClient` ([`indexer_client.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/indexer_client.py))

A thin HTTP client wrapping the read API. Every method raises
`IndexerUnavailable` on any network/HTTP error so the caller can transparently
fall back to the on-chain event-scan.

```python
from mycelium_sdk.indexer_client import IndexerClient, IndexerUnavailable

client = IndexerClient()  # uses constants.INDEXER_URL by default
try:
    result = client.list_agents(capability="vision", min_reputation=5)
except IndexerUnavailable:
    # transparent fallback to on-chain scan
    pass
```

### SDK — `HiveClient.discover_agents` ([`hive.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/hive.py))

```python
from mycelium import AgentContext, HiveClient

hive = HiveClient(AgentContext.read_only(network_type="testnet"))

# Default: prefer indexer, fall back to chain if unreachable
agents = hive.discover_agents(capability="vision")

# Force on-chain scan (skip the indexer)
agents = hive.discover_agents(capability="vision", prefer_indexer=False)

# Indexer speed + chain-verified addresses
agents = hive.discover_agents(capability="vision", verify=True)
```

The `verify=True` flow: query the indexer for speed, then `resolve_agent` each
returned name on-chain to confirm addresses. **DB speed, chain trust.**

### CLI

```bash
mycelium agents                           # instant when the indexer is up
mycelium agents --capability vision       # server-side filter
mycelium discover --capability nlp        # alias
```

Prints `"Querying indexer ..."` then instant results. Falls back to
`"Falling back to on-chain scan ..."` if the indexer is unreachable.

### IDE

The `/bounty` and `/agent` pages in the frontend can query the indexer API
for agent directory data and job listings.

---

## Access model & self-hosting

- **Hosted-first.** Mycelium runs one indexer at
  `https://mycelium-indexer.onrender.com`; the SDK/CLI/IDE point at its URL
  over HTTP. No download is required for normal use.
- **Not bundled in the pip metapackage.** Unlike the compiler (which runs
  locally), the indexer is a server; bundling it would drag `firebase-admin` +
  FastAPI into every client install for no client benefit.
- **Override URL:** set `MYCELIUM_INDEXER_URL` to point at your own deployment.
  The SDK constant is `DEFAULT_INDEXER_URL` in
  [`constants.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/constants.py#L58).
- **Sovereign path:**
  ```bash
  pip install mycelium-stellar[indexer]
  python -m indexer.worker --from-ledger <N>   # backfill, then tail
  uvicorn indexer.api:app --port 8080          # serve the read API
  ```

See [`indexer/DEPLOY.md`](file:///home/ansh/Mycelium/indexer/DEPLOY.md) for
Docker build, Render deployment (Blueprint + manual), and free-tier workarounds.

---

## Safety & verification model

| Property | How it's achieved |
|---|---|
| **Idempotent ingest** | Every upsert is keyed on a globally unique document id (agent name, event id, etc.). Re-ingest overwrites, never duplicates. |
| **Crash-safe resume** | Cursor persisted to `indexer_meta/cursor`. Restart resumes from `last_ledger + 1`. |
| **Full-state reproducibility** | Wipe all Firestore collections + re-backfill → identical state. |
| **Chain-verifiable** | Every API response carries `source_contract` + `as_of_ledger`. Clients can re-`resolve_agent` on-chain to verify any row. |
| **Trustless capability tags** | `POST /agents/{name}/capabilities` re-hashes submitted tags and rejects if they don't match the on-chain `capability_hash`. |
| **Monotonic memory anchors** | `_upsert_memory_anchor` only advances the version — a replayed older event never overwrites a newer one. |
| **Graceful degradation** | `IndexerClient` raises `IndexerUnavailable` on any error; `HiveClient` transparently falls back to the on-chain event-scan. The indexer is a cache, never a hard dependency. |

### Verification checklist

- Register agents on testnet → they appear via `GET /agents?capability=...` instantly.
- Wipe the Firestore collections + re-backfill → identical state.
- Kill the worker mid-run → restart resumes from the cursor doc with no dupes.
- `discover_agents` with the indexer URL down → falls back to the event-scan and
  still returns agents.
- `POST /agents/{name}/capabilities` with wrong tags → rejected (400).
- `POST /admin/ingest` without `X-Ingest-Token` → rejected (403).

## Release 0.5.0 — Stellar Mainnet Indexing & Network Partitioning

Version `0.5.0` upgrades the indexer to support indexing events from either Stellar Testnet or Stellar Mainnet, keeping records safely isolated within the same database:

* **Cursor Partitioning:** The cursor document ID is dynamically computed as `cursor_{network}` (e.g. `cursor_testnet` and `cursor_mainnet`) under `indexer_meta`. This ensures that backfills and tails on testnet do not interfere with mainnet logs.
* **Database Document Partitioning:** Every ingested document (agents, jobs, settlements, and memory anchors) is tagged with a `"network"` field (either `"testnet"` or `"mainnet"`).
* **API Route Filtering:** The `GET /agents` and `GET /jobs` endpoints support a `network` query parameter (e.g. `/agents?network=mainnet` or `/jobs?network=testnet`). When provided, results are filtered to return entries only from the requested network, enabling the Web IDE frontend to partition agent directory and job board displays completely.

---

## Related docs

- [`contracts.md`](./contracts.md) — the Hive Registry, Escrow, and JobBoard contracts that emit indexed events.
- [`memory.md`](./memory.md) — persistent agent memory (the `memory_anchored` events indexed here).
- [`sdk.md`](./sdk.md) — SDK core classes including `HiveClient` and `AgentContext`.
- [`indexer/DEPLOY.md`](file:///home/ansh/Mycelium/indexer/DEPLOY.md) — Docker build, Render deployment, free-tier workarounds.
