"""
Pure event-normalization for the indexer worker.

Turns a decoded Soroban event (topic + positional value) into a small,
datastore-agnostic record describing the upsert it implies. Kept free of any
Firestore / RPC dependency so it is fully unit-testable offline.

The Mycelium compiler emits `env.emit_event(topic, {k: v, ...})` as
`publish((topic,), (v, ...))` — the dict KEYS are dropped on-chain, so every
payload is positional. The orderings below mirror the `emit_event` calls in
`hive_registry.py`, `job_board_contract.py`, and `escrow_contract.py`.
"""

from typing import Any, Dict, List, Optional

from mycelium_sdk.events import address_to_str, decode_topics, decode_value

# Topics, grouped by the collection they feed.
AGENT_TOPIC = "agent_registered"
JOB_POSTED = "job_posted"
JOB_CLAIMED = "job_claimed"
SWARM_JOINED = "swarm_joined"
JOB_SUBMITTED = "job_submitted"
JOB_COMPLETED = "job_completed"
JOB_CANCELLED = "job_cancelled"
ESCROW_LOCKED = "escrow_locked"
ESCROW_RELEASED = "escrow_released"
ESCROW_SPLIT = "escrow_split"
ESCROW_REFUNDED = "escrow_refunded"

# job lifecycle topic -> status string stored on the job doc.
_JOB_STATUS = {
    JOB_CLAIMED: "claimed",
    JOB_SUBMITTED: "submitted",
    JOB_COMPLETED: "done",
    JOB_CANCELLED: "cancelled",
}
# escrow settlement topic -> settlement kind stored on the settlement doc.
_SETTLEMENT_KIND = {
    ESCROW_LOCKED: "locked",
    ESCROW_RELEASED: "released",
    ESCROW_SPLIT: "split",
    ESCROW_REFUNDED: "refunded",
}

ALL_TOPICS = (
    [AGENT_TOPIC, JOB_POSTED, SWARM_JOINED]
    + list(_JOB_STATUS)
    + list(_SETTLEMENT_KIND)
)


def _pos(value: Any, i: int) -> Any:
    """Positional element `i` of a decoded event value (list/tuple), or None."""
    if isinstance(value, (list, tuple)) and len(value) > i:
        return value[i]
    return None


def _int(x: Any) -> Optional[int]:
    return int(x) if x is not None else None


def normalize_event(event: Any, scval, stellar_xdr) -> Optional[Dict[str, Any]]:
    """
    Normalize one decoded event into an upsert record, or None if the event is
    not one we index / cannot decode.

    The returned dict always carries `kind` (the target collection), `topic`,
    `ledger`, `event_id`, and `contract` (the emitting contract id); the rest of
    the fields depend on `kind`:
      - "agent"      → name, address
      - "job_posted" → job_id, poster, bounty
      - "job_status" → job_id, status, agent (agent only for `claimed`)
      - "swarm"      → job_id, agent, share_bps
      - "settlement" → escrow, settlement_kind, amount, counterparty, count
    """
    topics = decode_topics(event, scval, stellar_xdr)
    if not topics:
        return None
    topic = str(topics[0])
    value = decode_value(event, scval, stellar_xdr)

    base = {
        "topic": topic,
        "ledger": getattr(event, "ledger", None),
        "event_id": getattr(event, "id", None),
        "contract": getattr(event, "contract_id", None),
    }

    if topic == AGENT_TOPIC:
        name = _pos(value, 0)
        if name is None:
            return None
        return {**base, "kind": "agent", "name": str(name),
                "address": address_to_str(_pos(value, 1))}

    if topic == JOB_POSTED:
        job_id = _int(_pos(value, 0))
        if job_id is None:
            return None
        return {**base, "kind": "job_posted", "job_id": job_id,
                "poster": address_to_str(_pos(value, 1)),
                "bounty": _int(_pos(value, 2))}

    if topic in _JOB_STATUS:
        job_id = _int(_pos(value, 0))
        if job_id is None:
            return None
        rec = {**base, "kind": "job_status", "job_id": job_id,
               "status": _JOB_STATUS[topic]}
        if topic == JOB_CLAIMED:
            rec["agent"] = address_to_str(_pos(value, 1))
        return rec

    if topic == SWARM_JOINED:
        job_id = _int(_pos(value, 0))
        if job_id is None:
            return None
        return {**base, "kind": "swarm", "job_id": job_id,
                "agent": address_to_str(_pos(value, 1)),
                "share_bps": _int(_pos(value, 2))}

    if topic in _SETTLEMENT_KIND:
        settlement_kind = _SETTLEMENT_KIND[topic]
        # escrow_split publishes (recipient_count, amount); the others publish
        # (counterparty_address, amount).
        if topic == ESCROW_SPLIT:
            counterparty, count = None, _int(_pos(value, 0))
        else:
            counterparty, count = address_to_str(_pos(value, 0)), None
        return {**base, "kind": "settlement", "escrow": base["contract"],
                "settlement_kind": settlement_kind,
                "amount": _int(_pos(value, 1)),
                "counterparty": counterparty, "count": count}

    return None


def sanitize_doc_id(event_id: Optional[str], ledger: Optional[int], index: int) -> str:
    """
    A stable, Firestore-safe document id for an event.

    The RPC event id (`<ledger>-<index>`) is globally unique and already
    Firestore-safe, so re-ingesting the same event overwrites its doc (idempotent
    upsert). Falls back to a synthesized id if the RPC omits one.
    """
    if event_id:
        return str(event_id).replace("/", "_")
    return f"{ledger or 0}-{index}"
