"""
`mycelium events` — show (or stream) a contract's on-chain events from RPC.

Defaults to the project's deployed contract (`[onchain].contract_id`). It scans
the RPC's retained ledger window and prints each event's topics + value decoded
to native Python. With `--follow` it polls for new events and prints them as they
land, until interrupted.

Soroban RPC only retains events for a bounded number of ledgers; registrations or
calls older than that horizon are not visible here.
"""

import sys
import time
from typing import Optional

from mycelium_cli.config import get_value

_PAGE_LIMIT = 100
_LEDGER_WINDOW = 16000
_FOLLOW_INTERVAL_SECONDS = 4


def _decode(event, scval, stellar_xdr):
    """Decode an event's topics and value to native Python, tolerating bad XDR."""
    def _native(x):
        try:
            return scval.to_native(stellar_xdr.SCVal.from_xdr(x))
        except (ValueError, AttributeError):
            return "<undecodable>"

    topics = [_native(t) for t in (event.topic or [])]
    value = _native(event.value) if event.value else None
    return topics, value


def _print_event(event, scval, stellar_xdr) -> None:
    topics, value = _decode(event, scval, stellar_xdr)
    ledger = getattr(event, "ledger", "?")
    topic_str = ", ".join(map(str, topics)) or "—"
    print(f"  [ledger {ledger}] {topic_str}  ->  {value}")


def run_events(
    contract: Optional[str] = None,
    network: Optional[str] = None,
    start_ledger: Optional[int] = None,
    follow: bool = False,
) -> None:
    from mycelium_sdk import AgentContext
    from stellar_sdk import scval
    from stellar_sdk import xdr as stellar_xdr
    from stellar_sdk.soroban_rpc import EventFilter, EventFilterType

    network = network or get_value("onchain", "network", "testnet")
    contract = contract or get_value("onchain", "contract_id")
    if not contract:
        print("Error: no contract id. Pass --contract C..., or deploy first.")
        sys.exit(1)

    rpc = AgentContext.read_only(network_type=network).soroban_rpc
    event_filter = EventFilter(
        event_type=EventFilterType.CONTRACT,
        contract_ids=[contract],
        topics=[["*"]],
    )

    latest = rpc.get_latest_ledger().sequence
    if start_ledger is None:
        probe = rpc.get_events(start_ledger=max(1, latest - 1), filters=[event_filter], limit=1)
        start_ledger = probe.oldest_ledger or max(1, latest - _LEDGER_WINDOW)

    print(f"[events] Scanning {contract} on {network} from ledger {start_ledger}...\n")
    cursor = _scan(rpc, event_filter, start_ledger, latest, scval, stellar_xdr)

    if not follow:
        return

    print("\n[events] Following new events (Ctrl-C to stop)...")
    try:
        while True:
            time.sleep(_FOLLOW_INTERVAL_SECONDS)
            page = rpc.get_events(cursor=cursor, filters=[event_filter], limit=_PAGE_LIMIT)
            for event in page.events or []:
                _print_event(event, scval, stellar_xdr)
                cursor = event.id
    except KeyboardInterrupt:
        print("\n[events] Stopped.")


def _scan(rpc, event_filter, lo, latest, scval, stellar_xdr) -> Optional[str]:
    """Print every event from `lo`..`latest`, returning the last cursor seen."""
    cursor = None
    count = 0
    while lo <= latest:
        hi = min(lo + _LEDGER_WINDOW - 1, latest)
        page_cursor = None
        while True:
            kwargs = {"filters": [event_filter], "limit": _PAGE_LIMIT}
            if page_cursor is None:
                kwargs["start_ledger"] = lo
                kwargs["end_ledger"] = hi
            else:
                kwargs["cursor"] = page_cursor
            page = rpc.get_events(**kwargs)
            events = page.events or []
            for event in events:
                _print_event(event, scval, stellar_xdr)
                page_cursor = cursor = event.id
                count += 1
            if len(events) < _PAGE_LIMIT:
                break
        lo = hi + 1
    if count == 0:
        print("  (no events in the retained window)")
    return cursor
