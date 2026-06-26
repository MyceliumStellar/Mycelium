"""
Shared Soroban event-scan utilities.

The Hive Registry keeps no on-chain list of names and the JobBoard keeps no
enumerable index of jobs; both are discovered by scanning the contracts'
emitted events back through the RPC's retained ledger window. That paging loop
— walk the retained range in sub-cap windows, paginate within a window by event
id — is identical for the SDK's `discover_agents`, the CLI, and the off-chain
indexer worker, so it lives here once.

`scan_contract_events` is generic over contract id(s) and topic, cursor-aware,
and wraps each RPC round-trip in `with_retry` so transient RPC errors don't
abort a long backfill. The `decode_*` / `parse_registration_event` helpers
normalize the XDR-encoded topics and values into native Python.
"""

from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence

from mycelium_sdk.rpc import with_retry

# Per-page event cap for the RPC getEvents pagination loop.
DEFAULT_PAGE_LIMIT = 100
# Soroban RPC scans a bounded ledger span per getEvents call (~16384 ledgers);
# walk the retained range in windows just under that cap.
DEFAULT_LEDGER_WINDOW = 16000
# Safety bound on how many windows one scan will walk (each is one RPC
# round-trip). At ~16k ledgers/window this covers ~1M ledgers — far past any
# real RPC retention horizon — without risking an unbounded loop.
DEFAULT_MAX_WINDOWS = 64

# Topic the registry contract publishes on every successful registration.
REGISTRATION_EVENT_TOPIC = "agent_registered"


def address_to_str(value: Any) -> Optional[str]:
    """Normalize a decoded address (stellar_sdk Address or str) to a G/C string."""
    if value is None:
        return None
    return getattr(value, "address", None) or str(value)


def bytes_to_str(value: Any) -> Any:
    """Decode bytes to a UTF-8 string; pass through if already str / not bytes."""
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value
    return value


def decode_topics(event: Any, scval, stellar_xdr) -> Optional[List[Any]]:
    """Decode an event's XDR topic list to native values, or None on failure."""
    try:
        return [scval.to_native(stellar_xdr.SCVal.from_xdr(t)) for t in event.topic]
    except (ValueError, AttributeError):
        return None


def decode_value(event: Any, scval, stellar_xdr) -> Any:
    """Decode an event's XDR value to a native Python value, or None on failure."""
    try:
        return scval.to_native(stellar_xdr.SCVal.from_xdr(event.value))
    except (ValueError, AttributeError):
        return None


def scan_contract_events(
    rpc,
    contract_ids: Sequence[str],
    *,
    start_ledger: Optional[int] = None,
    end_ledger: Optional[int] = None,
    topics: Optional[List[List[str]]] = None,
    page_limit: int = DEFAULT_PAGE_LIMIT,
    ledger_window: int = DEFAULT_LEDGER_WINDOW,
    max_windows: int = DEFAULT_MAX_WINDOWS,
    retry: Optional[Callable] = with_retry,
) -> Iterator[Any]:
    """
    Yield every contract event for `contract_ids` over a ledger range, oldest
    first, transparently handling the RPC's per-call ledger-span cap and
    per-window pagination.

    `start_ledger` defaults to the oldest ledger the RPC still retains (probed
    once); `end_ledger` defaults to the latest ledger. Topics default to the
    wildcard `[["*"]]` and are matched by the caller in Python — the on-chain
    topic's exact XDR encoding is not reliably reproducible client-side, so a
    pre-encoded symbol filter can silently drop every event.

    Each `get_events` round-trip is wrapped in `retry` (default `with_retry`)
    so a transient RPC error mid-scan retries rather than aborting a backfill.
    Pass `retry=None` to disable.
    """
    from stellar_sdk.soroban_rpc import EventFilter, EventFilterType

    event_filter = EventFilter(
        event_type=EventFilterType.CONTRACT,
        contract_ids=list(contract_ids),
        topics=topics or [["*"]],
    )

    def _get_events(**kwargs):
        if retry is not None:
            return retry(lambda: rpc.get_events(**kwargs), label="get_events")
        return rpc.get_events(**kwargs)

    latest = end_ledger if end_ledger is not None else rpc.get_latest_ledger().sequence
    if start_ledger is None:
        probe = _get_events(
            start_ledger=max(1, latest - 1), filters=[event_filter], limit=1
        )
        start_ledger = probe.oldest_ledger or max(1, latest - ledger_window)

    lo = start_ledger
    windows = 0
    while lo <= latest and windows < max_windows:
        hi = min(lo + ledger_window - 1, latest)
        cursor: Optional[str] = None
        while True:
            kwargs: Dict[str, Any] = {"filters": [event_filter], "limit": page_limit}
            if cursor is None:
                kwargs["start_ledger"] = lo
                kwargs["end_ledger"] = hi
            else:
                kwargs["cursor"] = cursor
            page = _get_events(**kwargs)
            events = page.events or []
            for event in events:
                yield event
                cursor = event.id
            if len(events) < page_limit:
                break
        lo = hi + 1
        windows += 1


def parse_registration_event(event: Any, scval, stellar_xdr) -> Optional[Dict[str, Any]]:
    """Extract `{name, public_key, ledger}` from an `agent_registered` event, or None."""
    topics = decode_topics(event, scval, stellar_xdr)
    if not topics or str(topics[0]) != REGISTRATION_EVENT_TOPIC:
        return None
    value = decode_value(event, scval, stellar_xdr)
    if value is None:
        return None
    # The contract publishes the data as (name, address).
    name = address = None
    if isinstance(value, (list, tuple)):
        if len(value) >= 1:
            name = value[0]
        if len(value) >= 2:
            address = value[1]
    elif isinstance(value, dict):
        name = value.get("name")
        address = value.get("address")
    if name is None:
        return None
    return {
        "name": str(name),
        "public_key": address_to_str(address),
        "ledger": getattr(event, "ledger", None),
    }
