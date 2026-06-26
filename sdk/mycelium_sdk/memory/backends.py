"""
Memory backends behind one interface.

`LocalVectorBackend` — SQLite + a tiny zero-dependency hashing embedder. Fully
offline, no cloud account, no heavy ML deps: the OSS "works on a laptop" default.
The embedder is a hashed bag-of-words (lexical) baseline; swap in a real model by
overriding `embed()` without touching the rest of the stack.

`SupermemoryBackend` — managed/cloud path keyed by the agent's G-address as the
container tag (ROADMAP §4). Stubbed here behind the same interface.

A backend is responsible only for the off-chain store. The canonical,
machine-independent memory blob it exports (`export_blob`) is what `AgentMemory`
hashes into the on-chain `memory_root`, so vectors are recomputed on import and
never need to be byte-identical across machines.
"""

import hashlib
import json
import math
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

_EMBED_DIM = 256


def _tokenize(text: str) -> List[str]:
    return [t for t in "".join(c.lower() if c.isalnum() else " " for c in text).split() if t]


def _hash_embed(text: str, dim: int = _EMBED_DIM) -> List[float]:
    """Deterministic hashed bag-of-words embedding, L2-normalized. Offline, zero-dep."""
    vec = [0.0] * dim
    for tok in _tokenize(text):
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _cosine(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class LocalVectorBackend:
    """SQLite-backed local memory store with offline semantic-ish recall."""

    name = "local"

    def __init__(self, owner: str, path: Optional[str] = None):
        self.owner = owner
        if path is None:
            base = os.path.join(os.path.expanduser("~"), ".mycelium", "memory")
            os.makedirs(base, exist_ok=True)
            path = os.path.join(base, f"{owner}.db")
        elif path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS memories ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL, tags TEXT NOT NULL)"
        )
        self._conn.commit()

    # ── writes / reads ───────────────────────────────────────────────────────
    def remember(self, content: str, tags: Optional[List[str]] = None) -> int:
        cur = self._conn.execute(
            "INSERT INTO memories (content, tags) VALUES (?, ?)",
            (content, json.dumps(sorted(tags or []))),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def recall(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        qv = _hash_embed(query)
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for row_id, content, tags in self._conn.execute("SELECT id, content, tags FROM memories"):
            score = _cosine(qv, _hash_embed(content))
            scored.append((score, {"id": row_id, "content": content, "tags": json.loads(tags), "score": score}))
        scored.sort(key=lambda s: s[0], reverse=True)
        return [r for _, r in scored[:k]]

    def all_records(self) -> List[Dict[str, Any]]:
        return [
            {"content": content, "tags": json.loads(tags)}
            for _, content, tags in self._conn.execute(
                "SELECT id, content, tags FROM memories ORDER BY id"
            )
        ]

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])

    # ── portability (blob = canonical, machine-independent) ──────────────────
    def export_blob(self) -> bytes:
        """Canonical bytes of the committed memory (content+tags, ordered). The
        thing `AgentMemory` hashes into `memory_root`. Vectors are NOT included —
        they're recomputed on import — so the blob is identical across machines."""
        payload = {"owner": self.owner, "records": self.all_records()}
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def import_blob(self, blob: bytes) -> int:
        """Replace local memory with the records in `blob`. Returns record count."""
        payload = json.loads(blob.decode("utf-8"))
        records = payload.get("records", [])
        self._conn.execute("DELETE FROM memories")
        self._conn.executemany(
            "INSERT INTO memories (content, tags) VALUES (?, ?)",
            [(r["content"], json.dumps(sorted(r.get("tags", [])))) for r in records],
        )
        self._conn.commit()
        return len(records)


class TieredBackend:
    """
    Use two stores at once (e.g. local laptop cache + Supermemory cloud).

    Writes mirror to BOTH; recall reads the `primary` (fast/local) first and
    tops up from `secondary` (durable/cloud), de-duplicated by content. The
    canonical blob is exported from `primary` (kept in sync by mirrored writes),
    so the on-chain `memory_root` is identical no matter which store you later
    read from — that's what makes the two interchangeable and verifiable.

        local = LocalVectorBackend(addr)
        cloud = SupermemoryBackend(addr, api_key=...)
        AgentMemory(ctx, backend=TieredBackend(local, cloud))
    """

    name = "tiered"

    def __init__(self, primary, secondary):
        self.primary = primary
        self.secondary = secondary
        self.owner = getattr(primary, "owner", None)

    def remember(self, content: str, tags: Optional[List[str]] = None) -> int:
        rid = self.primary.remember(content, tags)
        try:
            self.secondary.remember(content, tags)
        except Exception:
            pass  # cloud write is best-effort; the anchor still reflects primary
        return rid

    def recall(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        hits = list(self.primary.recall(query, k))
        if len(hits) < k:
            seen = {h["content"] for h in hits}
            try:
                for h in self.secondary.recall(query, k):
                    if h["content"] not in seen:
                        hits.append(h)
            except Exception:
                pass
        hits.sort(key=lambda h: h.get("score", 0.0), reverse=True)
        return hits[:k]

    def all_records(self) -> List[Dict[str, Any]]:
        return self.primary.all_records()

    def count(self) -> int:
        return self.primary.count()

    def export_blob(self) -> bytes:
        return self.primary.export_blob()

    def import_blob(self, blob: bytes) -> int:
        n = self.primary.import_blob(blob)
        try:
            self.secondary.import_blob(blob)
        except Exception:
            pass
        return n


class SupermemoryBackend:
    """
    Managed cloud backend keyed by the agent's G-address as `containerTag`
    (ROADMAP §4). Same interface as LocalVectorBackend. Requires a Supermemory
    API key; this is the revenue/scale path. Stub until wired to the API.
    """

    name = "supermemory"

    def __init__(self, owner: str, api_key: Optional[str] = None):
        self.owner = owner
        self.api_key = api_key or os.getenv("SUPERMEMORY_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "SupermemoryBackend needs a Supermemory API key "
                "(pass api_key= or set SUPERMEMORY_API_KEY). Use backend='local' for offline."
            )
        raise NotImplementedError(
            "SupermemoryBackend is not wired yet — use LocalVectorBackend (backend='local')."
        )
