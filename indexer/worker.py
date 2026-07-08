"""
Indexer worker — ingest on-chain Mycelium events into Firestore.

Chain stays the source of truth; this worker maintains a verifiable cache so
discovery is O(1) over full history instead of an O(N), retention-bounded
event-scan. It walks the registry / job-board / escrow events forward from a
persisted cursor, normalizes each (`parsing.normalize_event`), and idempotently
upserts the derived docs. Re-running from an earlier cursor is safe: every write
is keyed on the event's globally-unique id, so re-ingest overwrites rather than
duplicates.

Firestore layout (see plan.md P1):
  agents/{name}                 latest directory entry + capability_tags[]
  jobs/{job_id}                 job state (status advances with lifecycle events)
  jobs/{job_id}/members/{agent} swarm share
  settlements/{event_id}        escrow lock/release/split/refund (volume metrics)
  indexer_meta/cursor           {last_ledger, last_event_id}

Run:
  python -m indexer.worker                 # resume from cursor, then poll
  python -m indexer.worker --from-ledger N # backfill from ledger N
  python -m indexer.worker --once          # single catch-up pass, then exit
"""

import argparse
import os
import time
from typing import Any, Callable, Dict, Optional

from mycelium_sdk.events import scan_contract_events
from indexer import parsing

CURSOR_COLLECTION = "indexer_meta"
CURSOR_DOC = "cursor"
_POLL_INTERVAL_SECONDS = 10


def _board_address() -> Optional[str]:
    """JobBoard address: env `MYCELIUM_BOARD_ADDRESS` first (containers have no
    mycelium.toml), then the project's `[jobs].board_address` if present."""
    env = os.getenv("MYCELIUM_BOARD_ADDRESS")
    if env:
        return env
    try:
        from mycelium_cli.config import get_value

        return get_value("jobs", "board_address")
    except Exception:
        return None


class IndexerWorker:
    """
    Drives one ingest pass (or a polling loop) against an injected Firestore
    client and Soroban RPC. Both are injected so the worker is unit-testable
    with in-memory fakes.

    `resolve_agent` is an optional callable `name -> dict` used to enrich a newly
    seen agent with its on-chain endpoint/reputation/capability the first time;
    results are cached for the process lifetime.
    """

    def __init__(
        self,
        db,
        rpc,
        contract_ids: Dict[str, str],
        resolve_agent: Optional[Callable[[str], Dict[str, Any]]] = None,
        resolve_job: Optional[Callable[[int], Dict[str, Any]]] = None,
        network: str = "testnet",
    ):
        self.db = db
        self.rpc = rpc
        self.contract_ids = contract_ids
        self._resolve_agent = resolve_agent
        self._agent_cache: Dict[str, Dict[str, Any]] = {}
        self._resolve_job = resolve_job
        self._job_enriched: set = set()
        self.network = network

    # ── cursor ────────────────────────────────────────────────────────────────
    def _load_cursor(self) -> Optional[int]:
        cursor_doc = f"cursor_{self.network}"
        snap = self.db.collection(CURSOR_COLLECTION).document(cursor_doc).get()
        if getattr(snap, "exists", False):
            data = snap.to_dict() or {}
            return data.get("last_ledger")
        return None

    def _save_cursor(self, last_ledger: int, last_event_id: Optional[str]) -> None:
        cursor_doc = f"cursor_{self.network}"
        self.db.collection(CURSOR_COLLECTION).document(cursor_doc).set(
            {"last_ledger": last_ledger, "last_event_id": last_event_id}
        )

    # ── scanned contracts ───────────────────────────────────────────────────--
    def _scanned_contract_ids(self):
        ids = []
        for key in ("registry", "job_board", "memory_anchor"):
            if self.contract_ids.get(key):
                ids.append(self.contract_ids[key])
        ids.extend(self.contract_ids.get("escrows", []) or [])
        return ids

    # ── one ingest pass ─────────────────────────────────────────────────────--
    def run_once(self, from_ledger: Optional[int] = None) -> Dict[str, int]:
        """
        Ingest every event from the cursor (or `from_ledger`) to the chain tip.
        Returns counts per kind. Advances the cursor to the last event's ledger.
        """
        from stellar_sdk import scval
        from stellar_sdk import xdr as stellar_xdr

        start_ledger = from_ledger if from_ledger is not None else self._load_cursor()
        # +1 so we don't re-emit the cursor's own ledger; None lets the scanner
        # probe the RPC's oldest retained ledger.
        if start_ledger is not None:
            start_ledger = start_ledger + 1

        contracts = self._scanned_contract_ids()
        if not contracts:
            raise ValueError("IndexerWorker has no contracts to scan.")

        counts: Dict[str, int] = {}
        last_ledger = None
        last_event_id = None
        for i, event in enumerate(
            scan_contract_events(self.rpc, contracts, start_ledger=start_ledger)
        ):
            record = parsing.normalize_event(event, scval, stellar_xdr)
            last_ledger = getattr(event, "ledger", last_ledger)
            last_event_id = getattr(event, "id", last_event_id)
            if record is None:
                continue
            self._apply(record, i)
            counts[record["kind"]] = counts.get(record["kind"], 0) + 1

        if last_ledger is not None:
            self._save_cursor(last_ledger, last_event_id)
        return counts

    def run_forever(self, poll_interval: int = _POLL_INTERVAL_SECONDS) -> None:
        """Catch up, then poll forever. Ctrl-C to stop."""
        while True:
            counts = self.run_once()
            if counts:
                print(f"[indexer] ingested {counts}")
            time.sleep(poll_interval)

    # ── upserts ──────────────────────────────────────────────────────────────
    def _apply(self, rec: Dict[str, Any], index: int) -> None:
        kind = rec["kind"]
        if kind == "agent":
            self._upsert_agent(rec)
        elif kind == "job_posted":
            self._upsert_job_posted(rec)
        elif kind == "job_status":
            self._upsert_job_status(rec)
        elif kind == "swarm":
            self._upsert_swarm(rec)
        elif kind == "settlement":
            self._upsert_settlement(rec, index)
        elif kind == "memory_anchor":
            self._upsert_memory_anchor(rec)

    def _upsert_agent(self, rec: Dict[str, Any]) -> None:
        name = rec["name"]
        doc: Dict[str, Any] = {
            "address": rec.get("address"),
            "last_update_ledger": rec.get("ledger"),
        }
        # Enrich once from on-chain resolution (endpoint/reputation/tags).
        if name not in self._agent_cache and self._resolve_agent is not None:
            try:
                self._agent_cache[name] = self._resolve_agent(name) or {}
            except Exception:
                self._agent_cache[name] = {}
        details = self._agent_cache.get(name, {})
        for key in ("endpoint", "model", "role", "desc", "reputation", "capability_tags"):
            if details.get(key) is not None:
                doc[key] = details[key]
        if "capability_tags" not in doc:
            doc.setdefault("capability_tags", [])
        doc["network"] = self.network
        ref = self.db.collection("agents").document(name)
        if not getattr(ref.get(), "exists", False):
            doc["first_seen_ledger"] = rec.get("ledger")
        ref.set(doc, merge=True)

    def _upsert_job_posted(self, rec: Dict[str, Any]) -> None:
        job_id = rec["job_id"]
        doc: Dict[str, Any] = {
            "job_id": job_id,
            "poster": rec.get("poster"),
            "bounty": rec.get("bounty"),
            "status": "open",
            "posted_ledger": rec.get("ledger"),
        }
        # Enrich once with the immutable fields the event doesn't carry. The
        # event-driven status/agent set elsewhere stays authoritative. The job is
        # self-describing on-chain, so we also copy its title / description / spec
        # (checks + chosen judge panel) + judge here, so the bounty page can render
        # the real job without its own chain round-trip.
        if job_id not in self._job_enriched and self._resolve_job is not None:
            try:
                details = self._resolve_job(job_id) or {}
                for key in ("token", "mode", "escrow", "deadline", "judge",
                            "title", "description", "spec", "rubric_hash"):
                    if details.get(key) is not None:
                        doc[key] = details[key]
            except Exception:
                pass
            self._job_enriched.add(job_id)
        doc["network"] = self.network
        self.db.collection("jobs").document(str(job_id)).set(doc, merge=True)

    def _upsert_job_status(self, rec: Dict[str, Any]) -> None:
        doc: Dict[str, Any] = {"status": rec["status"], "last_update_ledger": rec.get("ledger")}
        if rec.get("agent") is not None:
            doc["agent"] = rec["agent"]
        doc["network"] = self.network
        self.db.collection("jobs").document(str(rec["job_id"])).set(doc, merge=True)

    def _upsert_swarm(self, rec: Dict[str, Any]) -> None:
        job_ref = self.db.collection("jobs").document(str(rec["job_id"]))
        job_ref.set({"status": "claimed", "mode": "swarm", "network": self.network}, merge=True)
        if rec.get("agent"):
            job_ref.collection("members").document(rec["agent"]).set(
                {"share_bps": rec.get("share_bps")}, merge=True
            )

    def _upsert_memory_anchor(self, rec: Dict[str, Any]) -> None:
        # Latest anchor per agent → O(1) "where is this agent's memory + version".
        # Monotonic version: only advance (events arrive oldest-first, but guard
        # against re-ingest of an older one overwriting a newer).
        ref = self.db.collection("memory_anchors").document(rec["owner"])
        existing = ref.get()
        prev = (existing.to_dict() or {}).get("version", 0) if getattr(existing, "exists", False) else 0
        if (rec.get("version") or 0) >= prev:
            ref.set(
                {
                    "owner": rec["owner"],
                    "version": rec.get("version"),
                    "last_anchor_ledger": rec.get("ledger"),
                    "network": self.network,
                },
                merge=True,
            )

    def _upsert_settlement(self, rec: Dict[str, Any], index: int) -> None:
        doc_id = parsing.sanitize_doc_id(rec.get("event_id"), rec.get("ledger"), index)
        self.db.collection("settlements").document(doc_id).set(
            {
                "escrow": rec.get("escrow"),
                "kind": rec.get("settlement_kind"),
                "amount": rec.get("amount"),
                "counterparty": rec.get("counterparty"),
                "count": rec.get("count"),
                "ledger": rec.get("ledger"),
                "network": self.network,
            }
        )


def build_default_worker(network: str = "testnet") -> IndexerWorker:
    """Wire a worker against the live RPC + Firestore using SDK defaults."""
    from mycelium_sdk import AgentContext, HiveClient
    from mycelium_sdk.constants import contract_address
    from indexer.firestore_client import get_firestore

    ctx = AgentContext.read_only(network_type=network)
    hive = HiveClient(ctx)

    def _resolve(name: str) -> Dict[str, Any]:
        details = hive.resolve_agent(name)
        details.setdefault("capability_tags", [])
        return details

    board = _board_address()

    resolve_job = None
    if board:
        from mycelium_sdk import JobBoardClient

        jobs = JobBoardClient(ctx, board)

        def resolve_job(job_id: int) -> Dict[str, Any]:
            return jobs.get_job(job_id)

    contracts = {
        "registry": contract_address("hive_registry", network),
        "job_board": board,
        "memory_anchor": os.getenv("MYCELIUM_MEMORY_ANCHOR_ADDRESS") or contract_address("memory_anchor", network),
    }
    return IndexerWorker(
        get_firestore(), ctx.soroban_rpc, contracts,
        resolve_agent=_resolve, resolve_job=resolve_job,
        network=network,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Mycelium off-chain indexer worker.")
    parser.add_argument("--network", default="testnet")
    parser.add_argument("--from-ledger", type=int, default=None,
                        help="Backfill from this ledger instead of the saved cursor.")
    parser.add_argument("--once", action="store_true",
                        help="Run a single catch-up pass and exit.")
    args = parser.parse_args()

    worker = build_default_worker(args.network)
    if args.once or args.from_ledger is not None:
        counts = worker.run_once(from_ledger=args.from_ledger)
        print(f"[indexer] ingested {counts}")
    else:
        worker.run_forever()


if __name__ == "__main__":
    main()
