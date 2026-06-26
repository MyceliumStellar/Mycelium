"""
Read-side data access for the indexer API.

`FirestoreStore` runs the actual Firestore queries; the API depends only on this
small interface, so routes are unit-tested with an in-memory fake store. Each
list method returns `(rows, next_cursor)` for cursor pagination.

Capability search uses Firestore `array-contains` on `capability_tags`;
reputation ordering + `min_reputation` filtering needs the composite index
declared in `firestore.indexes.json`.
"""

from typing import Any, Dict, List, Optional, Tuple

AGENTS = "agents"
JOBS = "jobs"
SETTLEMENTS = "settlements"
CURSOR = ("indexer_meta", "cursor")


class FirestoreStore:
    def __init__(self, db):
        self.db = db

    def as_of_ledger(self) -> Optional[int]:
        snap = self.db.collection(CURSOR[0]).document(CURSOR[1]).get()
        if getattr(snap, "exists", False):
            return (snap.to_dict() or {}).get("last_ledger")
        return None

    def list_agents(
        self,
        capability: Optional[str] = None,
        min_reputation: int = 0,
        limit: int = 50,
        start_after: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        from google.cloud.firestore_v1.base_query import FieldFilter
        from google.cloud.firestore_v1 import Query

        q = self.db.collection(AGENTS)
        if capability:
            q = q.where(filter=FieldFilter("capability_tags", "array_contains", capability))
        if min_reputation:
            q = q.where(filter=FieldFilter("reputation", ">=", min_reputation))
        q = q.order_by("reputation", direction=Query.DESCENDING).order_by("__name__")
        if start_after:
            snap = self.db.collection(AGENTS).document(start_after).get()
            if getattr(snap, "exists", False):
                q = q.start_after(snap)
        rows = [self._with_id(d) for d in q.limit(limit).stream()]
        return rows, (rows[-1]["name"] if len(rows) == limit else None)

    def get_agent(self, name: str) -> Optional[Dict[str, Any]]:
        snap = self.db.collection(AGENTS).document(name).get()
        return self._with_id(snap) if getattr(snap, "exists", False) else None

    def set_capability_tags(self, name: str, tags: List[str]) -> None:
        """Record verified plaintext capability tags for `array-contains` search."""
        self.db.collection(AGENTS).document(name).set(
            {"capability_tags": list(tags)}, merge=True
        )

    def list_jobs(
        self,
        status: Optional[str] = None,
        mode: Optional[str] = None,
        min_bounty: int = 0,
        limit: int = 50,
        start_after: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        from google.cloud.firestore_v1.base_query import FieldFilter
        from google.cloud.firestore_v1 import Query

        q = self.db.collection(JOBS)
        if status:
            q = q.where(filter=FieldFilter("status", "==", status))
        if mode:
            q = q.where(filter=FieldFilter("mode", "==", mode))
        if min_bounty:
            q = q.where(filter=FieldFilter("bounty", ">=", min_bounty))
        q = q.order_by("bounty", direction=Query.DESCENDING).order_by("__name__")
        if start_after:
            snap = self.db.collection(JOBS).document(start_after).get()
            if getattr(snap, "exists", False):
                q = q.start_after(snap)
        rows = [self._with_id(d) for d in q.limit(limit).stream()]
        return rows, (rows[-1]["job_id"] if len(rows) == limit else None)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        ref = self.db.collection(JOBS).document(str(job_id))
        snap = ref.get()
        if not getattr(snap, "exists", False):
            return None
        job = self._with_id(snap)
        members = []
        for m in ref.collection("members").stream():
            members.append({"agent": m.id, **(m.to_dict() or {})})
        if members:
            job["members"] = members
        return job

    def stats(self) -> Dict[str, Any]:
        """Aggregate counts + settled volume for the business-model dashboard."""
        agents = sum(1 for _ in self.db.collection(AGENTS).stream())
        jobs = sum(1 for _ in self.db.collection(JOBS).stream())
        settled = 0
        volume = 0
        for s in self.db.collection(SETTLEMENTS).stream():
            data = s.to_dict() or {}
            if data.get("kind") in ("released", "split"):
                settled += 1
                volume += int(data.get("amount") or 0)
        return {"agents": agents, "jobs": jobs, "settlements": settled, "volume_stroops": volume}

    @staticmethod
    def _with_id(snap) -> Dict[str, Any]:
        data = snap.to_dict() or {}
        data.setdefault("name", snap.id)
        if "job_id" not in data:
            data["job_id"] = snap.id
        return data
