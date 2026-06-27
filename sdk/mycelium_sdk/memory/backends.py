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


def canonical_blob(owner: str, records: List[Dict[str, Any]]) -> bytes:
    """
    Deterministic, backend-independent serialization of a memory set.

    Records are normalized to `{content, tags(sorted)}` and the LIST is sorted by
    (content, tags) so the bytes — and therefore the `memory_root` — are identical
    no matter the insertion order or which backend produced them. That identity is
    what makes local and Supermemory interchangeable behind one on-chain anchor.
    """
    norm = sorted(
        ({"content": r["content"], "tags": sorted(r.get("tags") or [])} for r in records),
        key=lambda r: (r["content"], r["tags"]),
    )
    return json.dumps({"owner": owner, "records": norm}, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


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
        """Canonical bytes of the committed memory — the thing `AgentMemory`
        hashes into `memory_root`. Vectors are NOT included (recomputed on
        import), so the blob is identical across machines and backends."""
        return canonical_blob(self.owner, self.all_records())

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
    Managed cloud backend over the real Supermemory API (https://api.supermemory.ai).

    Keyed by the agent's G-address as the `containerTag`, so every agent's memory
    is isolated by default (ROADMAP §4). Same interface as LocalVectorBackend —
    `remember` POSTs a document, `recall` runs semantic search, and `export_blob`
    reconstructs the canonical blob from the cloud so the on-chain `memory_root`
    matches the local backend's for the same memory set.

    Requires a Supermemory API key (pass `api_key=` or set `SUPERMEMORY_API_KEY`).
    """

    name = "supermemory"
    BASE_URL = "https://api.supermemory.ai"

    def __init__(self, owner: str, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 timeout: float = 20.0):
        self.owner = owner
        self.api_key = api_key or os.getenv("SUPERMEMORY_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "SupermemoryBackend needs a Supermemory API key "
                "(pass api_key= or set SUPERMEMORY_API_KEY). Use backend='local' for offline."
            )
        self.base_url = (base_url or os.getenv("SUPERMEMORY_BASE_URL") or self.BASE_URL).rstrip("/")
        self.timeout = timeout

    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        import requests

        resp = requests.post(f"{self.base_url}{path}", json=body, headers=self._headers(),
                             timeout=self.timeout)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def _get(self, path: str) -> Dict[str, Any]:
        import requests

        resp = requests.get(f"{self.base_url}{path}", headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    @staticmethod
    def _custom_id(content: str, tags: List[str]) -> str:
        """Deterministic id so re-ingesting the same memory upserts (no dupes)."""
        h = hashlib.sha256((content + "\x00" + ",".join(sorted(tags or []))).encode("utf-8"))
        return "myc-" + h.hexdigest()[:24]

    # ── writes / reads ───────────────────────────────────────────────────────
    def remember(self, content: str, tags: Optional[List[str]] = None) -> str:
        tags = sorted(tags or [])
        body = {
            "content": content,
            "containerTags": [self.owner],
            "metadata": {"mycelium_tags": ",".join(tags)},
            "customId": self._custom_id(content, tags),
        }
        res = self._post("/v3/documents", body)
        return str(res.get("id") or body["customId"])

    def recall(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        res = self._post("/v3/search", {"q": query, "containerTags": [self.owner], "limit": k})
        out = []
        for r in (res.get("results") or res.get("documents") or []):
            content = r.get("content") or r.get("memory") or ""
            if not content and isinstance(r.get("chunks"), list):
                content = " ".join(c.get("content", "") for c in r["chunks"])
            out.append({
                "content": content,
                "tags": self._tags_of(r),
                "score": float(r.get("score", 0.0)),
            })
        return out

    @staticmethod
    def _tags_of(doc: Dict[str, Any]) -> List[str]:
        meta = doc.get("metadata") or {}
        raw = meta.get("mycelium_tags", "")
        return [t for t in raw.split(",") if t] if isinstance(raw, str) else []

    def all_records(self) -> List[Dict[str, Any]]:
        """
        Rebuild the canonical record set from the cloud. The list endpoint
        (`/v3/documents/list`) is page-based and returns the documents under
        `memories` WITHOUT their `content`; the original text + our tags only
        come back on a per-document GET, so we list ids then fetch each one.
        """
        records: List[Dict[str, Any]] = []
        page_num = 1
        while True:
            body: Dict[str, Any] = {"containerTags": [self.owner], "limit": 200, "page": page_num}
            page = self._post("/v3/documents/list", body)
            docs = page.get("memories") or page.get("documents") or page.get("results") or []
            for d in docs:
                content = d.get("content")
                tags = self._tags_of(d)
                if content is None:  # list omits content → fetch the full document
                    doc_id = d.get("id") or d.get("customId")
                    if doc_id:
                        full = self._get(f"/v3/documents/{doc_id}")
                        content = full.get("content", "")
                        tags = self._tags_of(full) or tags
                records.append({"content": content or "", "tags": tags})
            pg = page.get("pagination") or {}
            total_pages = int(pg.get("totalPages") or 0)
            if not docs or (total_pages and page_num >= total_pages):
                break
            page_num += 1
        return records

    def count(self) -> int:
        return len(self.all_records())

    # ── portability (same canonical blob as the local backend) ───────────────
    def export_blob(self) -> bytes:
        return canonical_blob(self.owner, self.all_records())

    def import_blob(self, blob: bytes) -> int:
        payload = json.loads(blob.decode("utf-8"))
        records = payload.get("records", [])
        for r in records:
            self.remember(r["content"], r.get("tags", []))  # upserts via customId
        return len(records)
