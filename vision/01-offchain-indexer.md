# Mycelium — Off-Chain Indexer: Detailed Design

> **Status:** Design / pre-build. Ships **before** the grant pitch (you stated this).
> **Why it exists:** Today, agent discovery (`HiveClient.discover_agents`) scans the Soroban RPC's `getEvents` over the retained ledger window — see `sdk/mycelium_sdk/hive.py`. That approach has three hard limits that *cannot* scale to millions of agents:
>
> 1. **Retention bound.** Soroban RPC only retains events for a bounded ledger window (~24h–7d depending on the node). Agents registered before that horizon are **invisible** to discovery. The code itself documents this caveat.
> 2. **O(N) RPC cost.** Discovery walks up to 64 ledger windows × pages × an extra `resolve_agent` simulation **per agent**. With 1M agents this is millions of RPC round-trips per listing.
> 3. **No query.** You can't ask "all agents with capability `image-labeling` and reputation > 50 near endpoint X" — there's no index, only a full scan.
>
> The indexer turns discovery from an **O(N) chain scan** into an **O(1) database lookup**, makes *all* history queryable (not just the retention window), and adds capability/reputation/geo search. It is the single most important scaling component, and it requires **zero new on-chain code** — the contracts already emit the events it needs.

---

## 1. Design principle: the chain is the source of truth, the indexer is a cache

The indexer **never** holds authoritative state. Every row it serves is reconstructable by replaying on-chain events. This matters:

- If the indexer is wiped, you re-sync from genesis (or a checkpoint) and get identical state.
- Clients can always **verify** an indexer answer against the chain (the indexer returns the contract ID + ledger so the client can re-`resolve_agent` if it doesn't trust the cache).
- Multiple competing indexers can exist (yours, a community one, an agent's private one) and they must agree, because they consume the same events.

This is the same trust model as a blockchain explorer. **You are building the explorer + search engine for the agent economy.**

---

## 2. What it indexes (the event catalog already emitted today)

Every contract in the repo already emits structured events. The indexer is a consumer of these — **no contract changes required**:

| Source contract | Event | Indexed into |
|---|---|---|
| `HiveRegistry` | `agent_registered {name, address}` | `agents` table (name → address, + resolved capability/endpoint/model/role/desc/reputation) |
| `HiveRegistry` | reputation updates (via `update_reputation`) | `agents.reputation` (re-resolve on change) |
| `JobBoard` | `job_posted {job_id, poster, bounty}` | `jobs` table (status=open) |
| `JobBoard` | `job_claimed {job_id, agent}` | `jobs.status=claimed`, `job_claimants` |
| `JobBoard` | `swarm_joined {job_id, agent, share}` | `job_swarm_members` (agent, share_bps) |
| `JobBoard` | `job_submitted {job_id}` | `jobs.status=submitted` |
| `JobBoard` | `job_completed {job_id}` | `jobs.status=done` + completion record for reputation |
| `JobBoard` | `job_cancelled {job_id}` | `jobs.status=cancelled` |
| `Escrow` | lock / release / split / refund | `settlements` table (volume metrics → powers the business model in `03-business-model.md`) |

> Note: the registry's events publish `(name, address)`. Richer fields (endpoint, model, reputation) live in contract **storage**, so the indexer does one `resolve_agent` simulation **per newly-seen name** — not per query. After that the row is cached and served from the DB.

---

## 3. Architecture

```
┌──────────────┐   getEvents    ┌────────────────────┐   upsert    ┌──────────────┐
│ Soroban RPC  │ ◄───poll/cursor─│  Ingest Worker     │ ───────────►│  Postgres    │
│ (testnet/    │                │  (cursor-tracked,   │             │  (agents,    │
│  mainnet)    │ ──resolve sim──►│   idempotent)      │             │  jobs, etc.) │
└──────────────┘                └─────────┬──────────┘             └──────┬───────┘
                                          │ on new agent: resolve_agent          │
                                          ▼                                      │
                                  ┌────────────────┐                             │
                                  │ Reputation /    │                            │
                                  │ derived metrics │                            │
                                  └────────────────┘                            │
                                                                                ▼
                  ┌──────────────────┐   REST/GraphQL   ┌────────────────────────────┐
  SDK / CLI / IDE │  Query API        │ ◄───────────────│  Read API (FastAPI)         │
  (HiveClient)    │  GET /agents?cap=  │                 │  cached, paginated, filtered │
                  │  GET /jobs?status= │                 └────────────────────────────┘
                  └──────────────────┘
```

**Three components:**

### 3a. Ingest worker (the only thing that talks to the chain)
- A long-running Python process. Reuses your existing `mycelium_sdk` event-parsing logic (lift `_parse_registration_event` and the `getEvents` paging loop straight out of `hive.py` into a shared `mycelium_sdk/events.py`).
- **Cursor-tracked, not retention-bound.** It persists the last-processed `(ledger, event_id)` cursor in Postgres. On every tick it calls `getEvents` from the cursor forward, processes the page, advances the cursor. Because it runs *continuously* from the moment you deploy it, it captures **every** event before it falls out of the RPC's retention window — so the DB accumulates the *full* history the RPC itself can no longer serve.
- **Idempotent.** Every event has a unique `(ledger, tx_index, event_index)` id. Upserts key on that id, so reprocessing the same event (after a crash/restart) is a no-op. Safe to replay.
- **Resilient.** Reuses `mycelium_sdk.rpc.with_retry` for transient RPC failures.
- **Backfill mode.** A one-shot `--from-ledger N` that walks history forward to seed a fresh DB (or after schema migration).

### 3b. Postgres schema (start simple, index the query columns)
```sql
CREATE TABLE agents (
  name            TEXT PRIMARY KEY,
  address         TEXT NOT NULL,
  capability_hash BYTEA,
  capability_tags TEXT[],            -- denormalized for search (see §4)
  endpoint        TEXT,
  model           TEXT,
  role            TEXT,
  description     TEXT,
  reputation      BIGINT DEFAULT 0,
  first_seen_ledger BIGINT,
  last_update_ledger BIGINT,
  updated_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_agents_reputation ON agents (reputation DESC);
CREATE INDEX idx_agents_caps ON agents USING GIN (capability_tags);

CREATE TABLE jobs (
  job_id      BIGINT PRIMARY KEY,
  poster      TEXT, bounty NUMERIC, token TEXT,
  mode        TEXT, status TEXT, escrow TEXT,
  deadline    BIGINT, spec_uri TEXT, spec_hash BYTEA,
  posted_ledger BIGINT, updated_ledger BIGINT
);
CREATE INDEX idx_jobs_status ON jobs (status);
CREATE INDEX idx_jobs_open ON jobs (status) WHERE status = 'open';

CREATE TABLE job_swarm_members (job_id BIGINT, agent TEXT, share_bps INT, PRIMARY KEY (job_id, agent));
CREATE TABLE settlements (escrow TEXT, job_id BIGINT, amount NUMERIC, token TEXT, kind TEXT, ledger BIGINT, ts TIMESTAMPTZ);
CREATE TABLE cursor (id INT PRIMARY KEY DEFAULT 1, last_ledger BIGINT, last_event_id TEXT);
```

### 3c. Read API (FastAPI — lives next to your existing IDE backend)
Stateless, horizontally scalable, reads only Postgres (never the chain on the hot path):
```
GET  /agents?capability=image-labeling&min_reputation=50&limit=20&cursor=...
GET  /agents/{name}                       # O(1) replacement for resolve_agent
GET  /jobs?status=open&mode=swarm&min_bounty=10
GET  /jobs/{job_id}
GET  /stats                               # total agents, open jobs, settled volume (powers /business)
```
- Pagination by keyset (`WHERE name > :cursor ORDER BY name`), never OFFSET.
- Every response includes `source_contract` + `as_of_ledger` so clients can verify against the chain.
- Cache hot queries (open jobs, top agents) in Redis with a short TTL; the worker invalidates on write.

---

## 4. Capability search (the feature the chain literally cannot do)

The registry stores `capability_hash = sha256(sorted(tags))` — a *hash*, so you can resolve an exact tag set but **cannot search "agents that can do X"** on-chain. The indexer fixes this:

- The SDK already knows the plaintext tags at registration time (`_compute_capability_hash` in `hive.py`).
- **Option A (no contract change):** include the plaintext tags in the registration *event* or the `endpoint`/`desc` payload, OR have agents publish their tag list to a well-known endpoint the indexer fetches. The indexer stores `capability_tags TEXT[]` and serves GIN-indexed array search.
- **Option B (later):** add a `capabilities` event topic to the registry DSL that emits the plaintext tags. Backward-compatible.

Either way, **search is an indexer concern, not a chain concern** — which is correct, because search indexes don't belong on a metered ledger.

---

## 5. SDK integration — make it transparent and optional

`HiveClient` gains an indexer-aware fast path with graceful fallback:

```python
class HiveClient:
    def __init__(self, context, registry_address=None, indexer_url=None):
        self.indexer_url = indexer_url or context.config.get("indexer_url")

    def discover_agents(self, ..., prefer_indexer=True):
        if self.indexer_url and prefer_indexer:
            try:
                return self._discover_via_indexer(...)   # O(1) HTTP, full history, filterable
            except IndexerUnavailable:
                pass  # fall through
        return self._discover_via_events(...)            # today's on-chain scan (always works)
```

- **Trustless by default:** the indexer answer carries `address` + `name`; if the caller passes `verify=True`, the SDK re-runs `resolve_agent` on-chain for the returned names and drops any mismatch. You get DB speed with chain-grade trust on demand.
- **Self-hostable:** an agent operator can run their own indexer against the same events and point the SDK at it — no dependence on your hosted instance. This is critical for the decentralization story an SDF reviewer will probe.

---

## 6. Scaling the indexer itself (toward millions of agents)

| Concern | Approach |
|---|---|
| Single ingest worker is a SPOF | Worker is stateless except the cursor; run one active + one standby (leader lock on the `cursor` row). Ingest is naturally serial per contract — you don't need many writers. |
| Read throughput | Read API is stateless → scale horizontally behind a load balancer; Postgres read replicas; Redis cache for hot queries. |
| Postgres size at 1M+ agents | Agents table at 1M rows is trivial for Postgres. The volume is in `settlements`/job history → partition by ledger range; archive cold partitions. |
| Multiple contracts / multiple networks | One worker per (network, contract-set); namespace tables by network. |
| Re-orgs | Soroban has deterministic finality (no deep re-orgs like PoW); process events only after they're in a closed ledger. Keep a small reorg buffer (N ledgers) before marking final if you want belt-and-suspenders. |

---

## 7. Build plan (phased, ~2–3 weeks of focused work for two people)

**M1 — Ingest skeleton (week 1)**
- [ ] Extract event parsing + `getEvents` paging from `hive.py` into `mycelium_sdk/events.py` (shared by SDK and indexer).
- [ ] Postgres schema + migrations (`agents`, `jobs`, `cursor`).
- [ ] Cursor-tracked ingest worker: registry `agent_registered` → `agents` (resolve once per new name). Idempotent upserts.
- [ ] Backfill command (`indexer backfill --from-ledger N`).

**M2 — Jobs + settlements (week 2)**
- [ ] Ingest `JobBoard` events → `jobs`, `job_swarm_members`. Status transitions.
- [ ] Ingest `Escrow` settlement events → `settlements` (this feeds the business-model volume metrics).
- [ ] `/stats` endpoint.

**M3 — Read API + SDK integration (week 2–3)**
- [ ] FastAPI read API: `/agents`, `/agents/{name}`, `/jobs`, `/jobs/{id}`, keyset pagination, capability filter.
- [ ] `HiveClient` indexer fast-path with on-chain fallback + `verify=True`.
- [ ] CLI: `mycelium agents` uses the indexer when configured (instant), falls back to event-scan offline.
- [ ] Wire the IDE `/jobs` and agent feed to the read API.

**M4 — Hardening (pre-mainnet)**
- [ ] Leader-lock HA for the worker; health/metrics endpoints; reorg buffer.
- [ ] Deploy alongside the IDE backend (Render/Fly/your infra); managed Postgres.

---

## 8. What to say about this in the pitch

> "Discovery on-chain is retention-bounded and O(N) — it can't scale to millions of agents. Our off-chain indexer turns it into an O(1), fully-searchable lookup over the *complete* history, while staying trustless: every answer is verifiable against the chain, and anyone can run their own indexer off the same events. The contracts already emit everything it needs — no new on-chain code. **This is the explorer-and-search-engine for the agent economy.**"

This is also a **monetizable surface** — the hosted indexer is a revenue line in `03-business-model.md` (managed infra tier).
