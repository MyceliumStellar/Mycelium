"""
AgentMemory — durable, portable, verifiable agent memory.

Big mutable private data lives off-chain (a `*Backend`); a tiny commitment
(`memory_root`, `uri`, `version`) goes on-chain via the MemoryAnchor contract.
Reads/writes are free and fast; anchoring is lazy (checkpoints), so per-agent
on-chain cost stays constant regardless of memory size.

    mem = AgentMemory(ctx)                 # local backend, hosted anchor contract
    mem.remember("user prefers dark mode", tags=["pref"])
    mem.recall("ui preferences")           # off-chain semantic-ish search
    mem.anchor(uri="https://.../mem.json") # checkpoint: publish blob, commit root on-chain
    mem.verify()                           # recompute root, compare to chain
    mem.rehydrate()                        # on a fresh machine: load + verify from the anchor

`memory_root` is a flat SHA-256 of the backend's canonical blob (Merkle root is a
v2 enhancement). `uri` is where the blob is published so another machine can
fetch it; for the local backend you can also point it at a `file://`/path.
"""

import hashlib
import time
from typing import Any, Callable, Dict, List, Optional


def _content_root(blob: bytes) -> bytes:
    return hashlib.sha256(blob).digest()


class AnchoringPolicy:
    """
    The cost knob: when to spend an on-chain `set_anchor` tx.

    Anchoring is NOT per-write (that would put a tx on every `remember`). Instead
    you anchor at meaningful checkpoints — job completion and a periodic
    heartbeat — and only when the memory has actually changed since the last
    anchor. `min_writes` and `heartbeat_seconds` bound how often a heartbeat may
    fire; job completion always anchors if there are unanchored writes.

        policy = AnchoringPolicy(heartbeat_seconds=3600, min_writes=1)
        mem = AgentMemory(ctx, policy=policy)
    """

    def __init__(self, heartbeat_seconds: float = 3600.0, min_writes: int = 1):
        self.heartbeat_seconds = heartbeat_seconds
        self.min_writes = max(1, int(min_writes))


class AgentMemory:
    def __init__(self, context, backend="auto", anchor_address: Optional[str] = None,
                 backend_kwargs: Optional[Dict[str, Any]] = None,
                 policy: Optional[AnchoringPolicy] = None):
        """
        `backend` is either a name ("local" | "supermemory" | "auto") or a
        pre-built backend instance — pass a `TieredBackend(local, cloud)` to use
        a laptop cache and Supermemory together behind one anchor.
        """
        from mycelium_sdk.memory.anchor import MemoryAnchorClient
        from mycelium_sdk.memory.backends import LocalVectorBackend, SupermemoryBackend

        self.context = context
        self.owner = context.keypair.public_key
        self.anchor_client = MemoryAnchorClient(context, anchor_address)

        backend_kwargs = backend_kwargs or {}
        if isinstance(backend, str):
            if backend in ("auto", "local"):
                self.backend = LocalVectorBackend(self.owner, **backend_kwargs)
            elif backend == "supermemory":
                self.backend = SupermemoryBackend(self.owner, **backend_kwargs)
            else:
                raise ValueError(f"Unknown backend '{backend}' (use 'local', 'supermemory', 'auto', or a backend instance).")
        else:
            # A pre-built backend instance (e.g. TieredBackend) — must implement
            # remember/recall/export_blob/import_blob.
            self.backend = backend

        # Anchoring policy state (the cost knob — see AnchoringPolicy).
        self.policy = policy or AnchoringPolicy()
        self._writes_since_anchor = 0
        self._last_anchor_ts: Optional[float] = None

    # ── off-chain (no chain tx) ──────────────────────────────────────────────
    def remember(self, content: str, tags: Optional[List[str]] = None) -> int:
        """Store a memory off-chain. No chain transaction."""
        rid = self.backend.remember(content, tags)
        self._writes_since_anchor += 1
        return rid

    def recall(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        """Semantic-ish search over off-chain memory. No chain transaction."""
        return self.backend.recall(query, k)

    def memory_root(self) -> bytes:
        """SHA-256 root of the current committed memory state."""
        return _content_root(self.backend.export_blob())

    @property
    def is_dirty(self) -> bool:
        """True if memory changed since the last anchor (an anchor would do work)."""
        return self._writes_since_anchor > 0

    # ── on-chain commitment ──────────────────────────────────────────────────
    def anchor(self, uri: str = "", acl: bytes = b"",
               publish: Optional[Callable[[bytes], str]] = None,
               at: Optional[float] = None) -> int:
        """
        Checkpoint: compute `memory_root` and commit it (+`uri`) on-chain, bumping
        the version. `uri` is where the blob is fetchable from another machine
        (https / Supermemory / IPFS / a file path for local single-machine use).

        Pass `publish(blob) -> uri` to publish the canonical blob somewhere
        (e.g. upload to object storage) and use the returned location as the
        on-chain `uri` — keeps publish + commit atomic so the anchor never
        points at a blob that was never stored. Returns the new on-chain version.
        """
        if publish is not None:
            uri = publish(self.backend.export_blob())
        version = self.anchor_client.set_anchor(self.memory_root(), uri, acl)
        self._writes_since_anchor = 0
        self._last_anchor_ts = time.time() if at is None else at
        return version

    # ── anchoring policy hooks (job-complete + heartbeat; NOT per-write) ──────
    def on_job_complete(self, uri: str = "", acl: bytes = b"",
                        publish: Optional[Callable[[bytes], str]] = None) -> Optional[int]:
        """
        Anchor at a job-completion checkpoint. Anchors iff there are unanchored
        writes (so completing a job that touched no memory costs nothing).
        Returns the new version, or None if nothing needed anchoring.
        """
        if not self.is_dirty:
            return None
        return self.anchor(uri=uri, acl=acl, publish=publish)

    def heartbeat(self, uri: str = "", acl: bytes = b"",
                  publish: Optional[Callable[[bytes], str]] = None,
                  now: Optional[float] = None) -> Optional[int]:
        """
        Periodic heartbeat anchor (the cost knob). Anchors only when memory is
        dirty, at least `policy.min_writes` writes have accrued, AND at least
        `policy.heartbeat_seconds` have elapsed since the last anchor. Otherwise
        a no-op. Returns the new version, or None if it was throttled.
        """
        if self._writes_since_anchor < self.policy.min_writes:
            return None
        now = time.time() if now is None else now
        if self._last_anchor_ts is not None and \
                (now - self._last_anchor_ts) < self.policy.heartbeat_seconds:
            return None
        return self.anchor(uri=uri, acl=acl, publish=publish, at=now)

    def get_anchor(self) -> Optional[Dict[str, Any]]:
        """Read this agent's current on-chain anchor, or None if never anchored."""
        return self.anchor_client.get_anchor(self.owner)

    def verify(self) -> bool:
        """
        Recompute the local memory root and compare to the on-chain anchor.
        True iff the local memory is exactly the committed (latest) state.
        """
        anchor = self.get_anchor()
        if not anchor:
            return False
        return anchor["root"] == self.memory_root()

    def rehydrate(self, fetch=None) -> Dict[str, Any]:
        """
        Load memory from the on-chain anchor on a fresh machine: read the anchor,
        fetch the blob from its `uri`, verify the blob hashes to `memory_root`,
        and import it. Raises on a hash mismatch (tamper/rollback protection).

        `fetch(uri) -> bytes` overrides how the blob is retrieved (defaults to a
        small built-in https/file fetcher). Returns {version, records}.
        """
        anchor = self.get_anchor()
        if not anchor:
            raise RuntimeError("No on-chain anchor for this agent — nothing to rehydrate.")

        blob = (fetch or self._default_fetch)(anchor["uri"])
        if _content_root(blob) != anchor["root"]:
            raise ValueError(
                "Memory blob does not match the on-chain root — refusing to load "
                "(tampered, truncated, or stale)."
            )
        count = self.backend.import_blob(blob)
        return {"version": anchor["version"], "records": count}

    @staticmethod
    def _default_fetch(uri: str) -> bytes:
        if not uri:
            raise ValueError("Anchor has no uri; pass fetch= to rehydrate from your store.")
        if uri.startswith("http://") or uri.startswith("https://"):
            import requests

            resp = requests.get(uri, timeout=15)
            resp.raise_for_status()
            return resp.content
        path = uri[len("file://"):] if uri.startswith("file://") else uri
        with open(path, "rb") as f:
            return f.read()
