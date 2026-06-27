# Persistent Agent Memory — Codebase Guide

Agents are increasingly stateless and serverless — they spin up, do work, and die.
Mycelium gives them **durable, portable, verifiable memory** without putting the
data on-chain. The model in one line:

> **Big mutable private memory lives off-chain; a tiny commitment goes on-chain.**

Per-agent on-chain footprint is **constant and tiny regardless of memory size** —
just `(memory_root, uri, version, acl)`. A hundred bytes on the ledger, no
matter if the agent remembers one fact or a million documents.

---

## Architecture

```
                     ┌──────────────────────────────────────────┐
                     │         Off-chain memory stores          │
                     │                                          │
                     │  ┌────────────────────┐                  │
                     │  │ LocalVectorBackend │  SQLite + hashed │
                     │  │ (default, offline) │  bag-of-words    │
                     │  │ ~/.mycelium/memory/│  embedder        │
                     │  │    {G-addr}.db     │                  │
                     │  └─────────┬──────────┘                  │
                     │            │                             │
                     │  ┌─────────┴──────────┐                  │
                     │  │  TieredBackend     │  writes mirror   │
                     │  │  (both at once)    │  to both; recall │
                     │  │                    │  reads primary   │
                     │  └─────────┬──────────┘  first           │
                     │            │                             │
                     │  ┌─────────┴──────────┐                  │
                     │  │SupermemoryBackend  │  Supermemory     │
                     │  │ (managed cloud)    │  v3 API          │
                     │  │ containerTag =     │  (real semantic  │
                     │  │   agent G-address  │  search)         │
                     │  └────────────────────┘                  │
                     └──────────────────┬───────────────────────┘
                                        │
                                        │ export_blob() → canonical
                                        │ bytes (JSON, normalized,
                                        │ sorted, no vectors)
                                        │
                          ┌─────────────▼─────────────┐
                          │      AgentMemory          │
                          │  (sdk/memory/agent_memory) │
                          │                           │
                          │  remember()  → off-chain  │
                          │  recall()    → off-chain  │
                          │  memory_root → SHA-256    │
                          │  anchor()   → on-chain tx │
                          │  verify()   → compare     │
                          │  rehydrate() → load+check │
                          └─────────────┬─────────────┘
                                        │
                          ┌─────────────▼─────────────┐
                          │   MemoryAnchorClient      │
                          │  (sdk/memory/anchor.py)   │
                          │  set_anchor() → tx        │
                          │  get_anchor() → read      │
                          │  get_version() → read     │
                          └─────────────┬─────────────┘
                                        │ Soroban calls
                          ┌─────────────▼─────────────┐
                          │   MemoryAnchor contract   │
                          │  (memory_anchor.py DSL)   │
                          │  Testnet: CAC27VKJ…IXJSB  │
                          │                           │
                          │  Storage per agent:       │
                          │    root:{owner} → Bytes   │
                          │    uri:{owner}  → Bytes   │
                          │    acl:{owner}  → Bytes   │
                          │    ver:{owner}  → U64     │
                          │    has:{owner}  → Bool    │
                          │                           │
                          │  Events:                  │
                          │    memory_anchored         │
                          │    {owner, version}        │
                          └─────────────┬─────────────┘
                                        │ events consumed by
                          ┌─────────────▼─────────────┐
                          │   Off-chain Indexer       │
                          │  memory_anchors/{owner}   │
                          │  → GET /memory/{owner}    │
                          └───────────────────────────┘
```

---

## The pieces — detailed

| Piece | File | What it does |
|---|---|---|
| **MemoryAnchor contract** | [`memory_anchor.py`](file:///home/ansh/Mycelium/memory_anchor.py) | On-chain commitment store. `set_anchor(owner, memory_root, uri, acl)` with `owner.require_auth()` + monotonic `version`. Views: `get_anchor`, `get_version`, `is_anchored`. Emits `memory_anchored {owner, version}`. |
| **MemoryAnchorClient** | [`sdk/memory/anchor.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/memory/anchor.py) | Thin Python wrapper over the contract. Real Soroban calls via `AgentContext.call_contract`. `set_anchor()` → tx, `get_anchor()` → simulated read, `get_version()` → simulated read. |
| **AgentMemory** | [`sdk/memory/agent_memory.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/memory/agent_memory.py) | The developer API: `remember`, `recall`, `anchor`, `verify`, `rehydrate`, `on_job_complete`, `heartbeat`. Owns the backend, the anchor client, and the anchoring policy. |
| **LocalVectorBackend** | [`sdk/memory/backends.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/memory/backends.py#L65-L132) | SQLite + zero-dependency hashed bag-of-words embedder. Default path: `~/.mycelium/memory/{G-address}.db`. |
| **SupermemoryBackend** | [`sdk/memory/backends.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/memory/backends.py#L196-L323) | Real wiring to the Supermemory v3 API (`api.supermemory.ai`). Requires `SUPERMEMORY_API_KEY`. |
| **TieredBackend** | [`sdk/memory/backends.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/memory/backends.py#L135-L193) | Combines two backends: writes mirror to both; recall reads primary first, tops up from secondary. Canonical blob exported from primary. |
| **AnchoringPolicy** | [`sdk/memory/agent_memory.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/memory/agent_memory.py#L30-L46) | The cost knob: controls when to spend an on-chain `set_anchor` tx. Parameters: `heartbeat_seconds`, `min_writes`. |
| **Indexer integration** | [`indexer/worker.py`](file:///home/ansh/Mycelium/indexer/worker.py#L226-L241) | Indexes `memory_anchored` events → `GET /memory/{owner}` (latest version per agent, O(1)). |

Deployed on testnet at `CAC27VKJEPDJJNI36NP7D7VH6WCHT6N5EITKSKPZIQNWA2VPEPBIXJSB`
(also in [`constants.MEMORY_ANCHOR_ADDRESS`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/constants.py#L20)
and `mycelium.toml [memory].anchor_address`).

---

## On-chain contract — [`memory_anchor.py`](file:///home/ansh/Mycelium/memory_anchor.py)

Written in the Mycelium DSL and compiled with our own compiler:

```bash
python -m mycelium_compiler.main memory_anchor.py -o build/memory_anchor.wasm
```

### Contract interface

| Function | Signature | Auth | Purpose |
|---|---|---|---|
| `set_anchor` | `(owner: Address, memory_root: Bytes, uri: Bytes, acl: Bytes) → U64` | `owner.require_auth()` | Commit the off-chain memory state. Stores root hash, fetch URI, and ACL. Bumps monotonic `version`. Emits `memory_anchored {owner, version}`. Returns new version. |
| `get_anchor` | `(owner: Address) → Map` | None (view) | Returns `{root, uri, acl, version}`. Reverts with `NOT_ANCHORED` if never anchored. |
| `get_version` | `(owner: Address) → U64` | None (view) | Returns current version (0 if never anchored). |
| `is_anchored` | `(owner: Address) → Bool` | None (view) | Returns whether `owner` has ever anchored. |

### Storage layout

Per-agent storage slots (keyed by `str(owner)`):

| Key pattern | Type | Value |
|---|---|---|
| `root:{owner}` | `Bytes` | SHA-256 root of the committed memory state |
| `uri:{owner}` | `Bytes` | Where to fetch the blob (https / IPFS / file://) |
| `acl:{owner}` | `Bytes` | Access control (opaque to the chain) |
| `ver:{owner}` | `U64` | Monotonic version counter |
| `has:{owner}` | `Bool` | Whether the agent has ever anchored |

**On-chain footprint per agent: ~200 bytes** regardless of memory size. The
contract stores no memory content — only the commitment.

### Event

`memory_anchored` emits `{owner: Address, version: U64}` on every `set_anchor`.
Consumed by the off-chain indexer for O(1) `GET /memory/{owner}` lookups.

---

## Developer API — [`AgentMemory`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/memory/agent_memory.py)

### Constructor

```python
AgentMemory(
    context,                              # AgentContext (wallet + RPC)
    backend="auto",                       # "local" | "supermemory" | "auto" | backend instance
    anchor_address=None,                  # override contract address
    backend_kwargs=None,                  # passed to backend constructor
    policy=None,                          # AnchoringPolicy (cost knob)
)
```

`backend="auto"` defaults to `LocalVectorBackend`. Pass a pre-built
`TieredBackend(local, cloud)` to use a laptop cache and Supermemory together
behind one anchor.

### Off-chain operations (no chain transaction, no cost)

```python
from mycelium import AgentContext, AgentMemory

ctx = AgentContext(".mycelium/wallet.json", network_type="testnet", passphrase="...")
mem = AgentMemory(ctx)

# Store a memory (off-chain, instant, free)
rid = mem.remember("user prefers concise answers", tags=["pref"])

# Semantic-ish search (off-chain, instant, free)
results = mem.recall("how should I answer", k=5)
# Returns: [{"id": 1, "content": "...", "tags": [...], "score": 0.87}, ...]

# Current SHA-256 root of committed memory state
root = mem.memory_root()  # bytes

# Has memory changed since last anchor?
mem.is_dirty  # True if writes have accrued
```

### On-chain commitment (one Soroban tx per anchor)

```python
# Basic anchor: compute root, commit on-chain, bump version
version = mem.anchor(uri="https://store.example.com/mem.json")

# Publish atomically: upload blob, then anchor with the returned URI
version = mem.anchor(publish=lambda blob: upload_to_object_store(blob))
# publish(blob) is called first → returns the URI → then set_anchor is called
# with that URI. If publish fails, the anchor is never written — so the chain
# never points at a blob that wasn't stored.

# Optional ACL (opaque bytes, meaning is up to you)
version = mem.anchor(uri="...", acl=b"G...,G...")

# Read the current on-chain anchor
anchor = mem.get_anchor()
# Returns: {"root": bytes, "uri": "https://...", "acl": bytes, "version": 4}
# Or None if never anchored.
```

### Verification

```python
# Recompute local root, compare to on-chain anchor
assert mem.verify()  # True iff local memory == on-chain commitment

# If someone tampered with the off-chain store, verify() returns False
```

`memory_root` is a flat SHA-256 of the backend's **canonical blob**. The
canonical blob normalizes records to `{content, tags(sorted)}` and sorts the
list, so the bytes — and therefore the root — are identical regardless of
insertion order or which backend produced them. Vectors are **not** in the blob
(recomputed on import), so it is byte-identical across machines.

### Portability — rehydrate on a fresh machine

```python
# Machine B, same wallet, empty local store:
result = mem.rehydrate()
# → reads the on-chain anchor (get_anchor)
# → fetches the blob from the stored URI (https / file:// / custom fetcher)
# → re-hashes the blob → compares to on-chain root
# → REFUSES to load if hashes don't match (tampered / truncated / stale)
# → imports records into the local backend
# Returns: {"version": 4, "records": 127}

# Custom fetcher (e.g. for IPFS or S3)
result = mem.rehydrate(fetch=lambda uri: my_s3_client.download(uri))
```

**Tamper protection:** if even one byte of the blob is modified, `rehydrate()`
raises `ValueError`. The monotonic `version` on-chain prevents rollback/replay
attacks (an older blob has a different root).

**Built-in fetcher:** supports `https://...` (via `requests`) and `file://...`
paths. Override with `fetch=` for custom stores.

---

## Anchoring policy — the cost knob

Anchoring is **not per-write** (that would put a tx on every `remember`). The
`AnchoringPolicy` controls when anchor transactions are actually sent:

```python
from mycelium import AgentContext, AgentMemory, AnchoringPolicy

policy = AnchoringPolicy(
    heartbeat_seconds=3600,   # minimum interval between heartbeat anchors
    min_writes=1,             # minimum writes before a heartbeat anchors
)
mem = AgentMemory(ctx, policy=policy)
```

### Policy hooks

| Method | When it anchors | When it's a no-op |
|---|---|---|
| `mem.anchor()` | Always (explicit checkpoint) | — |
| `mem.on_job_complete()` | Iff `is_dirty` (unanchored writes exist) | Memory hasn't changed since last anchor |
| `mem.heartbeat()` | Iff dirty AND `≥ min_writes` AND `≥ heartbeat_seconds` since last anchor | Any condition not met |

```python
# After every job completion:
mem.remember("completed job #42, delivered 3 images", tags=["job"])
version = mem.on_job_complete(uri="...")  # anchors because is_dirty
version = mem.on_job_complete(uri="...")  # None — not dirty anymore

# Periodic heartbeat (call from your agent loop):
mem.heartbeat(uri="...")  # anchors only if dirty + interval elapsed
```

`is_dirty` tells you whether an anchor would do any work. The `_writes_since_anchor`
counter resets to 0 on every successful anchor; `_last_anchor_ts` tracks the
wall-clock time of the last anchor for heartbeat throttling.

---

## Backends — deep dive

All backends implement the same interface:

```python
class Backend:
    def remember(content: str, tags: list) -> int|str    # store
    def recall(query: str, k: int) -> list[dict]         # search
    def all_records() -> list[dict]                       # enumerate
    def count() -> int                                    # count
    def export_blob() -> bytes                            # canonical blob
    def import_blob(blob: bytes) -> int                   # replace all
```

They all export the **same canonical blob** (`canonical_blob()` in
[`backends.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/memory/backends.py#L48-L62))
for the same memory set — so the on-chain root is backend-independent and
they're interchangeable behind one anchor.

### Canonical blob format

```json
{
  "owner": "GABCDEF...",
  "records": [
    {"content": "deadline is July 1", "tags": ["project"]},
    {"content": "user prefers dark mode", "tags": ["pref"]}
  ]
}
```

Records are normalized: `tags` are sorted, and the record list is sorted by
`(content, tags)`. JSON serialization uses `sort_keys=True` and compact
separators `(",",":")`. This ensures byte-identical output regardless of
insertion order or which backend produced it. **Vectors are not included** —
they are recomputed on import.

### `LocalVectorBackend` (default, fully offline)

| Aspect | Detail |
|---|---|
| **Storage** | SQLite database at `~/.mycelium/memory/{G-address}.db` |
| **Schema** | `CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL, tags TEXT NOT NULL)` |
| **Embedder** | Hashed bag-of-words: tokenize → MD5-hash each token → accumulate in a 256-dim vector → L2-normalize. Zero dependencies (no numpy, no ML). |
| **Recall** | Cosine similarity between query embedding and every stored record. In-memory full scan — fine for thousands of records, not for millions. |
| **Pluggable embedding** | Override `embed()` (currently `_hash_embed`) to plug in a real model (sentence-transformers, OpenAI embeddings, etc.) without touching the rest of the stack. |
| **Path override** | `LocalVectorBackend(owner, path="/custom/path.db")` or `path=":memory:"` for tests. |

### `SupermemoryBackend` (managed cloud)

| Aspect | Detail |
|---|---|
| **API** | Supermemory v3 (`https://api.supermemory.ai`) |
| **Isolation** | Keyed by the agent's G-address as the `containerTag`. Each agent's memory is isolated by default. |
| **Auth** | Bearer token via `api_key=` or `SUPERMEMORY_API_KEY` env var. |
| **`remember`** | `POST /v3/documents` with a deterministic `customId` = `myc-` + SHA256(content + tags)[:24]. Idempotent upsert — re-ingesting the same memory never duplicates. |
| **`recall`** | `POST /v3/search` with `containerTags` filter. Real semantic search. |
| **`all_records`** | `POST /v3/documents/list` (paginated, returns `memories` without content) → `GET /v3/documents/{id}` per doc to fetch content + tags. Rebuilds the full record set so `export_blob` matches the local backend's canonical blob. |
| **`import_blob`** | Iterates records and calls `remember()` per record. Idempotent via `customId`. |

### `TieredBackend(primary, secondary)` — "use both"

```python
from mycelium_sdk.memory import LocalVectorBackend, SupermemoryBackend, TieredBackend

backend = TieredBackend(
    LocalVectorBackend(ctx.keypair.public_key),       # fast, offline
    SupermemoryBackend(ctx.keypair.public_key),       # durable, semantic
)
mem = AgentMemory(ctx, backend=backend)
```

| Operation | Behavior |
|---|---|
| `remember` | Writes to primary; mirrors to secondary (best-effort, failure is non-fatal). |
| `recall` | Reads primary first; if fewer than `k` results, tops up from secondary (de-duplicated by content). Sorts by score descending. |
| `all_records` | Returns primary's records only. |
| `export_blob` | Exports from primary. The canonical blob (and thus `memory_root`) is primary-authoritative. |
| `import_blob` | Imports into primary; mirrors to secondary (best-effort). |

This lets a local laptop cache and the Supermemory cloud sit behind one
verifiable on-chain anchor. The primary (local) is always consistent with the
anchor; the secondary (cloud) is best-effort and can be rebuilt from the blob.

---

## CLI

```bash
# Off-chain operations (no tx, no cost)
mycelium memory remember "deadline is 2026-07-01" --tag project
mycelium memory recall   "when is the deadline"
mycelium memory status   # local count + on-chain version/root/uri

# On-chain checkpoint
mycelium memory anchor   --publish mem.json    # commit root (file:// uri)

# Verification
mycelium memory verify                          # local == on-chain?

# Portability (on a fresh machine)
mycelium memory rehydrate                        # load + verify from anchor

# Cloud backend
mycelium memory remember "..." --backend supermemory  # needs SUPERMEMORY_API_KEY
```

---

## Indexer integration

The `memory_anchored` event emitted by `set_anchor` is consumed by the
off-chain indexer ([`indexer/worker.py`](file:///home/ansh/Mycelium/indexer/worker.py)):

1. **Worker ingests** the event and upserts `memory_anchors/{owner}` with the
   latest `(version, last_anchor_ledger)`. Monotonic: older versions are
   silently dropped.
2. **Read API** serves `GET /memory/{owner}` — O(1) lookup of an agent's latest
   anchor version and ledger, without a chain call.
3. **Use case:** agent B wants to know if agent A's memory is fresh before
   trusting it. Query the indexer → get version + ledger → compare to the blob's
   root.

---

## Trust model — what's verified where

| Check | Where | How |
|---|---|---|
| **"This memory blob matches the commitment"** | Client (SDK) | `verify()` re-hashes the local blob with SHA-256, compares to the on-chain `memory_root`. |
| **"This blob is the latest version"** | On-chain | Monotonic `version` on the contract. An older blob has a different root. |
| **"Only the owner can update the anchor"** | On-chain | `owner.require_auth()` in `set_anchor`. No one else can overwrite the commitment. |
| **"The blob hasn't been tampered with in transit"** | Client (SDK) | `rehydrate()` fetches the blob, re-hashes, rejects on mismatch. |
| **"The blob is the same across backends"** | Design guarantee | `canonical_blob()` normalizes content+tags, sorts deterministically. Vectors excluded. Same input → identical bytes → identical root. |

---

## Portability demo ([`memory_demo.py`](file:///home/ansh/Mycelium/memory_demo.py))

Runs the full proof on testnet:

1. **Machine A** writes memories, publishes the blob, and `anchor()`s the root.
2. **Machine B** (fresh, empty store, same wallet) `rehydrate()`s — reads the
   anchor, fetches the blob, re-hashes, verifies, and resumes.
3. **Tamper** a byte of the blob → `rehydrate()` / `verify()` reject it.

```bash
python memory_demo.py
```

This doubles as the pitch demo for stateless / serverless agents: kill the
process, move hosts, rehydrate from the anchor, keep going.

---

## Package exports ([`memory/__init__.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/memory/__init__.py))

```python
from mycelium_sdk.memory import (
    AgentMemory,             # the developer API
    AnchoringPolicy,         # the cost knob
    MemoryAnchorClient,      # direct contract access
    LocalVectorBackend,      # offline SQLite
    SupermemoryBackend,      # managed cloud
    TieredBackend,           # both at once
)
```

Via the top-level `mycelium` package:

```python
from mycelium import AgentMemory, AnchoringPolicy
```

---

## Related docs

- [`indexer.md`](./indexer.md) — the off-chain indexer that indexes `memory_anchored` events.
- [`contracts.md`](./contracts.md) — the Hive Registry, Escrow, and JobBoard contracts.
- [`sdk.md`](./sdk.md) — SDK core classes including `AgentContext`.
