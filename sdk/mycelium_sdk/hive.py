"""
HiveClient — directory resolution against the global Hive Registry contract.

Maps unique agent names to their on-chain identity (wallet address, capability
hash, service endpoint, reputation). Registration and resolution are real
Soroban calls routed through `AgentContext.call_contract`; there is no mocking.
If the configured registry address is the non-deployed placeholder, those calls
fail loudly at the RPC layer rather than returning fabricated data.
"""

import hashlib
from typing import Any, Dict, List, Optional

from mycelium_sdk.constants import HIVEMIND_REGISTRY_ADDRESS

# Topic the registry contract publishes on every successful registration
# (`env.emit_event("agent_registered", {"name": ..., "address": ...})`). Agent
# discovery works by scanning these events back through the RPC's retained
# ledger window — the registry keeps no on-chain list of names.
REGISTRATION_EVENT_TOPIC = "agent_registered"
# Per-page event cap for the RPC getEvents pagination loop.
_EVENT_PAGE_LIMIT = 100
# Soroban RPC scans a bounded ledger span per getEvents call (~16384 ledgers).
# We walk the retained range in windows just under that cap.
_LEDGER_WINDOW = 16000
# Safety bound on how many windows we'll walk in one discovery (each is one RPC
# round-trip). At ~16k ledgers/window this covers ~800k ledgers — far past any
# real retention horizon — without risking an unbounded loop.
_MAX_WINDOWS = 64


def _address_to_str(value: Any) -> Optional[str]:
    """Normalize a decoded address (stellar_sdk Address or str) to a G/C string."""
    if value is None:
        return None
    # stellar_sdk.address.Address exposes `.address`
    return getattr(value, "address", None) or str(value)


def _bytes_to_str(value: Any) -> Any:
    """Decode endpoint bytes to a UTF-8 string; pass through if already str."""
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value
    return value


class HiveClient:
    def __init__(self, context, registry_address: Optional[str] = None):
        self.context = context
        # Allow per-deployment override (e.g. from mycelium.toml [registry]).
        self.registry_address = registry_address or HIVEMIND_REGISTRY_ADDRESS

    def register(self, unique_name: str, capability_tags: List[str], endpoint: str, model: str = "", role: str = "", desc: str = ""):
        """
        Register `unique_name` on-chain with a capability hash, service
        endpoint, model tier, role, and description. Returns the TxResult of the
        registration transaction. Raises on name collision.
        """
        capability_hash = self._compute_capability_hash(capability_tags)
        # endpoint, model, role, desc are sent as raw UTF-8 bytes so they land as
        # Soroban Bytes values.
        return self.context.call_contract(
            contract_id=self.registry_address,
            function_name="register_agent",
            args=[
                unique_name,
                self.context.keypair.public_key,
                capability_hash,
                endpoint.encode("utf-8"),
                model.encode("utf-8"),
                role.encode("utf-8"),
                desc.encode("utf-8")
            ],
        )

    def resolve_agent(self, unique_name: str) -> Dict[str, Any]:
        """
        Resolve `unique_name` to its registered directory entry. Read-only
        (simulated, no fee). Returns a dict with public_key, capability_hash,
        endpoint, model, role, desc, and reputation.
        """
        raw = self.context.call_contract(
            contract_id=self.registry_address,
            function_name="resolve_agent",
            args=[unique_name],
            read_only=True,
        )
        return self._parse_metadata(raw)

    def discover_agents(
        self,
        start_ledger: Optional[int] = None,
        resolve: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Discover every agent registered on the Hive Registry by scanning the
        registry contract's `agent_registered` events through the RPC's retained
        ledger window.

        Returns a list of directory entries, newest registration first. Each
        entry has at least `name` and `public_key`; when `resolve=True` (the
        default) each name is additionally resolved on-chain to attach its
        current `endpoint`, `reputation`, and `capability_hash`.

        Caveat: Soroban RPC only retains events for a bounded number of ledgers
        (see `oldest_ledger` in the result of the underlying call). Agents
        registered before that window are not discoverable this way; pass an
        explicit `start_ledger` to widen or narrow the scan.
        """
        from stellar_sdk import scval
        from stellar_sdk import xdr as stellar_xdr
        from stellar_sdk.soroban_rpc import EventFilter, EventFilterType

        rpc = self.context.soroban_rpc
        event_filter = EventFilter(
            event_type=EventFilterType.CONTRACT,
            contract_ids=[self.registry_address],
            # Wildcard the topic and match in Python: the on-chain topic's exact
            # XDR encoding is not reliably reproducible client-side, so filtering
            # by a pre-encoded symbol can silently drop every event.
            topics=[["*"]],
        )

        latest = rpc.get_latest_ledger().sequence
        if start_ledger is None:
            # Probe once to learn the oldest ledger the RPC still retains, then
            # walk the full retained range from there.
            probe = rpc.get_events(
                start_ledger=max(1, latest - 1), filters=[event_filter], limit=1
            )
            start_ledger = probe.oldest_ledger or max(1, latest - _LEDGER_WINDOW)

        # Walk the ledger range in windows (the RPC scans only a bounded span per
        # call), paginating within a window by event id when it overflows the
        # page limit. De-duplicate by name, newest registration winning.
        latest_by_name: Dict[str, Dict[str, Any]] = {}
        lo = start_ledger
        windows = 0
        while lo <= latest and windows < _MAX_WINDOWS:
            hi = min(lo + _LEDGER_WINDOW - 1, latest)
            cursor: Optional[str] = None
            while True:
                kwargs: Dict[str, Any] = {
                    "filters": [event_filter],
                    "limit": _EVENT_PAGE_LIMIT,
                }
                if cursor is None:
                    kwargs["start_ledger"] = lo
                    kwargs["end_ledger"] = hi
                else:
                    kwargs["cursor"] = cursor
                page = rpc.get_events(**kwargs)
                events = page.events or []
                for event in events:
                    parsed = self._parse_registration_event(event, scval, stellar_xdr)
                    if parsed is not None:
                        # Events arrive oldest-first across windows, so a later
                        # registration of the same name overwrites the earlier.
                        latest_by_name[parsed["name"]] = parsed
                    cursor = event.id
                if len(events) < _EVENT_PAGE_LIMIT:
                    break
            lo = hi + 1
            windows += 1

        # Newest registration first.
        agents = list(reversed(list(latest_by_name.values())))
        if not resolve:
            return agents

        for agent in agents:
            try:
                details = self.resolve_agent(agent["name"])
            except Exception:
                # A name seen in events but no longer resolvable (e.g. retention
                # edge) still surfaces with the data we have from the event.
                continue
            agent.update(details)
        return agents

    @staticmethod
    def _parse_registration_event(event: Any, scval, stellar_xdr) -> Optional[Dict[str, Any]]:
        """Extract `{name, public_key}` from an `agent_registered` event, or None."""
        try:
            topics = [
                scval.to_native(stellar_xdr.SCVal.from_xdr(t)) for t in event.topic
            ]
        except (ValueError, AttributeError):
            return None
        if not topics or str(topics[0]) != REGISTRATION_EVENT_TOPIC:
            return None
        try:
            value = scval.to_native(stellar_xdr.SCVal.from_xdr(event.value))
        except (ValueError, AttributeError):
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
            "public_key": _address_to_str(address),
            "ledger": getattr(event, "ledger", None),
        }

    @staticmethod
    def _parse_metadata(raw: Any) -> Dict[str, Any]:
        """Normalize the registry's return value (map or positional tuple)."""
        if raw is None:
            raise KeyError("Agent name not registered in the Hive Registry.")

        if isinstance(raw, dict):
            address = raw.get("address", raw.get("addr"))
            capability = raw.get("capability", raw.get("capability_hash", raw.get("cap")))
            endpoint = raw.get("endpoint")
            model = raw.get("model")
            role = raw.get("role")
            desc = raw.get("desc", raw.get("description"))
            reputation = raw.get("reputation", 0)
        elif isinstance(raw, (list, tuple)):
            if len(raw) == 4:
                address, capability, endpoint, reputation = raw
                model = role = desc = None
            else:
                # Padded to size 7: address, capability, endpoint, model, role, desc, reputation
                padded = list(raw) + [None] * 7
                address, capability, endpoint, model, role, desc, reputation = padded[:7]
        else:
            raise TypeError(f"Unexpected registry return type: {type(raw).__name__}")

        return {
            "public_key": _address_to_str(address),
            "capability_hash": capability,
            "endpoint": _bytes_to_str(endpoint),
            "model": _bytes_to_str(model),
            "role": _bytes_to_str(role),
            "desc": _bytes_to_str(desc),
            "reputation": int(reputation) if reputation is not None else 0,
        }

    @staticmethod
    def _compute_capability_hash(tags: List[str]) -> bytes:
        """Deterministic SHA-256 of the sorted, comma-joined capability tags."""
        serialized_tags = ",".join(sorted(tags)).encode("utf-8")
        return hashlib.sha256(serialized_tags).digest()
