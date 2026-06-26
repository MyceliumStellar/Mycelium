# Mycelium — Persistent Agent Memory: Detailed Design

> **Status:** Design / pre-build (post-grant build is fine; this doc is the plan you reference in the pitch).
> **The question you asked:** *"For stateful agents, on-chain memory will be so costly — we can't put memory on chain, right?"*
>
> **Answer: Correct. You must NOT put agent memory on-chain. And you don't need to.** This document is the architecture that gives agents durable, portable, verifiable memory while keeping essentially *all* of the bytes off-chain. The pattern is already named in your ROADMAP ("off-chain memory, on-chain commitment") — this is the concrete, buildable version of it.

---

## 1. Why on-chain memory is the wrong primitive (the reasoning to give a reviewer)

Soroban storage is **metered, public, and rent-bearing**. Putting an agent's memory on-chain fails on every axis:

| Property of agent memory | Property of on-chain storage | Conflict |
|---|---|---|
| Large (MBs–GBs of conversation logs, embeddings, documents) | Tiny, expensive per byte, rent-bearing | Cost explodes; rent must be paid forever |
| Frequently mutated (every interaction) | Each write is a fee-bearing transaction + ledger close latency | Unusable write latency & cost |
| Often private (proprietary data, user PII) | Fully public, permanent, immutable | Privacy violation; GDPR-hostile |
| High-dimensional vectors for semantic recall | No vector index, no similarity search | Can't do the one operation memory needs |

**Conclusion:** the chain is the wrong place for the *data*. But the chain is the *right* place for one tiny thing: a **commitment** — a cryptographic proof of *what* the memory is and *who* controls it, so memory becomes **portable, verifiable, and access-controlled** across runs, servers, and even across different agents.

So: **big mutable private data off-chain; tiny immutable verifiable pointer on-chain.** That's the whole design.

---

## 2. The model: off-chain memory, on-chain commitment

```
        ┌─────────────────────────────┐         ┌──────────────────────────────┐
 Agent ►│  Off-chain memory store      │ ◄─────► │  On-chain MemoryAnchor (tiny) │
        │  • working / episodic         │  anchor │  • memory_root (hash)         │
        │  • semantic vectors           │  +verify│  • uri (where to fetch)       │
        │  • document fragments         │         │  • version (monotonic)        │
        │  large · private · fast · mut │         │  • acl (who may read/write)   │
        └─────────────────────────────┘         └──────────────────────────────┘
              99.99% of the bytes                   ~5 fields, a few hundred bytes
```

- **Off-chain:** the actual memory — vectors, facts, logs. Stored in a vector DB. This is where reads/writes happen at app speed and app cost (~free).
- **On-chain `MemoryAnchor`:** one small contract entry per agent holding `(memory_root, uri, version, acl)`. Updated **lazily** — not every thought, but at checkpoints. A few hundred bytes, written occasionally. This is what makes memory *trustable and portable* without paying to store the memory itself.

---

## 3. What goes on-chain vs off-chain (be precise — reviewers will push here)

| Data | Location | Why |
|---|---|---|
| Conversation logs, episodic events | **Off-chain** | Large, mutable, often private |
| Semantic vectors / embeddings | **Off-chain** | Huge; need similarity search the chain can't do |
| Document fragments, RAG corpus | **Off-chain** | Large, possibly proprietary |
| `memory_root` = Merkle/hash root of the committed memory state | **On-chain** | ~32 bytes; lets anyone verify the off-chain blob wasn't tampered with |
| `uri` = where to fetch the memory (Supermemory container, IPFS CID, https) | **On-chain** | Tiny; makes memory *portable* — any server can find it |
| `version` = monotonic counter | **On-chain** | Prevents rollback/replay; defines "latest" |
| `acl` = who may read/write (addresses / capability) | **On-chain** | Access control enforced by the ledger, not a server you must trust |

> The on-chain footprint per agent is **constant and tiny**, regardless of whether the agent has 10 memories or 10 million. That's the scaling property.

---

## 4. Memory tiering (how an agent actually uses it)

Three tiers, matching how the ROADMAP describes it, made concrete:

1. **Working memory** — in-process, current session. Never touches chain or even the store unless promoted. Free, fast, ephemeral.
2. **Short-term / episodic** — recent runs, persisted to the off-chain store, **not** anchored on every write (too frequent). Anchored at session end or every N events.
3. **Long-term / semantic** — durable facts and embeddings. Anchored on-chain **lazily** at checkpoints (e.g., end of a job, or every K updates), so the on-chain `version` bumps occasionally, not constantly.

**Anchoring policy = the cost knob.** You tune *how often* you write the on-chain anchor to trade verifiability-freshness against fee cost. Default: anchor at job completion + a periodic heartbeat. An agent doing millions of micro-updates anchors maybe once per task — so on-chain cost stays negligible even at huge memory volumes.

---

## 5. Sovereign identity = the container key (ties memory to the agent's wallet)

The agent's Stellar public key (`G-address`) is the **sovereign identifier** for its memory:

- It's the `containerTag` in Supermemory (your ROADMAP already specifies this) → data is isolate-by-default per agent.
- It's the `acl` owner in the `MemoryAnchor` → only the agent (or addresses it grants) can update the anchor, enforced on-chain via `require_auth()` (the same primitive the registry and job board already use).
- **Portability:** because the anchor is on-chain and keyed by the agent's address, the agent can rehydrate its memory on *any* server — pull the anchor, read `uri` + `memory_root`, fetch the blob, verify the hash, resume. This is what makes **stateless/serverless agents** possible (see §7).

---

## 6. The `MemoryAnchor` contract (authored in your own DSL)

Small, in the same style as `hive_registry.py` and `job_board_contract.py`:

```python
@contract
class MemoryAnchor:
    @external
    def set_anchor(self, owner: Address, memory_root: Bytes, uri: Bytes, acl: Bytes) -> U64:
        owner.require_auth()                       # only the agent updates its own memory
        version = self.storage.get("ver:" + owner, U64(0)) + U64(1)
        self.storage.set("root:" + owner, memory_root)
        self.storage.set("uri:" + owner, uri)
        self.storage.set("acl:" + owner, acl)
        self.storage.set("ver:" + owner, version)
        self.env.emit_event("memory_anchored", {"owner": owner, "version": version})
        return version

    @view
    def get_anchor(self, owner: Address) -> Map: ...   # root, uri, acl, version
```

- Reuses everything you already have: `require_auth`, `Bytes`, events, storage-key composition, `emit_event`.
- The `memory_anchored` event is consumed by the **indexer** (`01-offchain-indexer.md`) so "find this agent's latest memory anchor" is an O(1) lookup.
- **No new compiler features needed** — these are all DSL primitives shipped in v0.1.0.

---

## 7. SDK interface: `AgentMemory` (the developer-facing API)

```python
class AgentMemory:
    def __init__(self, ctx: AgentContext, backend="auto"):
        # backend: "local" (SQLite + local embeddings, offline) | "supermemory" (cloud) | "auto"
        self.owner = ctx.keypair.public_key

    def remember(self, content, tags=None): ...        # write to off-chain store (no chain tx)
    def recall(self, query, k=5): ...                  # semantic search off-chain (no chain tx)
    def anchor(self): ...                               # checkpoint: compute memory_root, set_anchor on-chain
    def rehydrate(self): ...                            # read anchor on-chain → fetch blob → verify root → load
    def verify(self): ...                               # recompute root, compare to on-chain anchor → bool
```

**Two backends behind one interface (this is the key product decision):**

- **`LocalVectorBackend`** — SQLite + local embeddings. Zero dependencies, fully offline, free. The developer can build & test agents with no cloud account. This is your **"works on a laptop"** story and the OSS default.
- **`SupermemoryBackend`** — Supermemory as the cloud-scale semantic engine (per ROADMAP §4), keyed by the agent's `G-address` as `containerTag`. This is the **managed, scalable** path and a **revenue surface** (see business model).

Both produce the same `memory_root`, so an agent can be developed locally and promoted to cloud without changing its anchors.

---

## 8. Verification flow (why this is trustworthy, not just cheap)

The point of the on-chain anchor is **tamper-evidence and portability**, not storage. The flow:

1. Agent writes memories off-chain (free, fast).
2. At a checkpoint, SDK computes `memory_root` = hash/Merkle-root of the committed memory state, calls `set_anchor` → on-chain `version` bumps.
3. Later (different server, different run, or *another agent* with read ACL): read the anchor → get `uri` + `memory_root` → fetch the blob from `uri` → **recompute the hash and compare to `memory_root`**. If it matches, the data is provably the committed state; if not, reject.

This means an agent (or a counterparty) can **trust memory it didn't itself store**, because the chain attests to the root. Combined with `version`, you also get **rollback protection** — nobody can serve you a stale memory and claim it's current.

> Merkle-root option: if you want *partial* verification (prove one fact is in memory without fetching all of it), make `memory_root` a Merkle root over memory chunks. Then a recall can come with a Merkle proof. This is a v2 enhancement — start with a flat content hash.

---

## 9. Build plan

**M1 — Local memory + anchor contract**
- [ ] Author + compile `memory_anchor.py` (your DSL), deploy to testnet, record id.
- [ ] `LocalVectorBackend` (SQLite + a small local embedding model).
- [ ] `AgentMemory.remember` / `recall` against the local backend (no chain).

**M2 — Anchoring + verification**
- [ ] `AgentMemory.anchor()` → compute root, `set_anchor` on-chain.
- [ ] `AgentMemory.rehydrate()` / `verify()` → read anchor, fetch, hash-check.
- [ ] Anchoring policy hooks (on job-complete, every-K-writes, manual).

**M3 — Supermemory backend**
- [ ] `SupermemoryBackend` behind the same interface; `G-address` as `containerTag`.
- [ ] Indexer consumes `memory_anchored` events → "latest anchor per agent" lookup.

**M4 — Portability demo**
- [ ] Agent writes memory on machine A, anchors; **rehydrates on machine B**, verifies root, resumes. (This is a *great* pitch demo for the "stateless/serverless agents" story.)

---

## 10. The crisp answers for the pitch (memorize these)

- **"Can you put agent memory on-chain?"** → "No — and you shouldn't. On-chain storage is metered, public, and rent-bearing; memory is large, mutable, and often private. We put the *memory* off-chain and only a tiny *commitment* on-chain — a hash, a URI, a version, and an ACL. Constant, tiny on-chain footprint no matter how much the agent remembers."
- **"Then why touch the chain at all?"** → "Because the on-chain anchor makes memory **portable, verifiable, and access-controlled** without trusting any server. An agent can resume on any machine and *prove* the memory it loaded is the real, latest state. That's something a plain database can't give you."
- **"How does it scale to millions of agents?"** → "Memory volume is off-chain, so it scales like any cloud datastore. On-chain, each agent is a few hundred bytes, anchored lazily at checkpoints — so a million agents is a million tiny, infrequently-updated entries, not a million live databases on the ledger."
- **"Stateful vs stateless agents?"** → "Stateless agents rehydrate from their anchor each run (perfect for serverless scale); stateful agents keep warm memory and anchor periodically. Same interface, your choice per deployment."

---

## 11. Relationship to the other docs
- The `memory_anchored` event is indexed by **`01-offchain-indexer.md`** → O(1) "find an agent's latest memory."
- The **`SupermemoryBackend`** (managed memory) and anchoring throughput are revenue surfaces in **`03-business-model.md`**.
- Lazy anchoring is part of the **`04-scaling-to-millions.md`** cost story — it keeps per-agent on-chain cost ~constant.
