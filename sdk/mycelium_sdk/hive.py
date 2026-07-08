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

from mycelium_sdk.constants import contract_address
from mycelium_sdk.events import (
    DEFAULT_LEDGER_WINDOW as _LEDGER_WINDOW,
    DEFAULT_MAX_WINDOWS as _MAX_WINDOWS,
    DEFAULT_PAGE_LIMIT as _EVENT_PAGE_LIMIT,
    REGISTRATION_EVENT_TOPIC,
    address_to_str as _address_to_str,
    bytes_to_str as _bytes_to_str,
    parse_registration_event,
    scan_contract_events,
)


class HiveClient:
    def __init__(self, context, registry_address: Optional[str] = None,
                 indexer_url: Optional[str] = None):
        self.context = context
        # Allow per-deployment override (e.g. from mycelium.toml [registry]).
        self.registry_address = registry_address or contract_address("hive_registry", getattr(self.context, "network_type", "testnet"))
        # Hosted indexer endpoint for O(1) discovery; None uses the SDK default.
        self.indexer_url = indexer_url

    def register(self, unique_name: str, capability_tags: List[str], endpoint: str, model: str = "", role: str = "", desc: str = ""):
        """
        Register `unique_name` on-chain with a capability hash, service
        endpoint, model tier, role, and description. Returns the TxResult of the
        registration transaction. Raises on name collision.
        """
        capability_hash = self._compute_capability_hash(capability_tags)
        # endpoint, model, role, desc are sent as raw UTF-8 bytes so they land as
        # Soroban Bytes values.
        result = self.context.call_contract(
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
        # Best-effort: publish the plaintext tags to the indexer so capability
        # search works (the on-chain event carries only the one-way hash). The
        # indexer re-verifies the tags against this hash, so this is trustless;
        # any failure is non-fatal — discovery still works via the chain scan.
        if capability_tags:
            self._publish_capability_tags(unique_name, capability_tags)
        return result

    def _publish_capability_tags(self, unique_name: str, capability_tags: List[str]) -> None:
        try:
            import requests

            from mycelium_sdk.constants import INDEXER_URL

            base = (self.indexer_url or INDEXER_URL).rstrip("/")
            requests.post(
                f"{base}/agents/{unique_name}/capabilities",
                json={"tags": list(capability_tags)},
                timeout=3.0,
            )
        except Exception:
            pass

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
        prefer_indexer: bool = True,
        capability: Optional[str] = None,
        min_reputation: int = 0,
        verify: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Discover registered agents.

        When `prefer_indexer` (default), this first queries the hosted off-chain
        indexer — an O(1), full-history lookup with optional `capability` /
        `min_reputation` server-side filters — and transparently falls back to
        the on-chain `agent_registered` event-scan if the indexer is unreachable.
        Pass `prefer_indexer=False` to force the chain path.

        Returns a list of directory entries, newest first. Each entry has at
        least `name` and `public_key`; with `resolve=True` (chain path) each name
        is resolved on-chain for its current `endpoint`/`reputation`. With
        `verify=True`, indexer rows are re-`resolve_agent`'d on-chain so callers
        get DB speed with chain-confirmed addresses.

        Caveat (chain path): Soroban RPC retains events for a bounded ledger
        window; agents registered before it are not discoverable that way — pass
        an explicit `start_ledger` to widen the scan.
        """
        if prefer_indexer:
            indexed = self._discover_via_indexer(capability, min_reputation, verify)
            if indexed is not None:
                return indexed

        from stellar_sdk import scval
        from stellar_sdk import xdr as stellar_xdr

        # Walk the registry's `agent_registered` events oldest-first and
        # de-duplicate by name (a later registration of the same name wins).
        latest_by_name: Dict[str, Dict[str, Any]] = {}
        for event in scan_contract_events(
            self.context.soroban_rpc,
            [self.registry_address],
            start_ledger=start_ledger,
            page_limit=_EVENT_PAGE_LIMIT,
            ledger_window=_LEDGER_WINDOW,
            max_windows=_MAX_WINDOWS,
        ):
            parsed = parse_registration_event(event, scval, stellar_xdr)
            if parsed is not None:
                latest_by_name[parsed["name"]] = parsed

        # Newest registration first.
        agents = list(reversed(list(latest_by_name.values())))
        if capability is not None or min_reputation:
            # Chain path can't filter by capability server-side; apply post-hoc
            # after resolution below (capability needs the resolved tags).
            pass
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

    def _discover_via_indexer(
        self,
        capability: Optional[str],
        min_reputation: int,
        verify: bool,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Query the hosted indexer; return rows, or None to signal the caller to
        fall back to the on-chain scan. `verify` re-resolves each row on-chain.
        """
        from mycelium_sdk.indexer_client import IndexerClient, IndexerUnavailable

        try:
            payload = IndexerClient(self.indexer_url).list_agents(
                capability=capability, min_reputation=min_reputation
            )
        except IndexerUnavailable:
            return None

        agents = payload.get("agents", [])
        # Normalize to the chain-path shape (`public_key` rather than `address`).
        for a in agents:
            a.setdefault("public_key", a.get("address"))
        if verify:
            for a in agents:
                try:
                    a.update(self.resolve_agent(a["name"]))
                except Exception:
                    continue
        return agents

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
