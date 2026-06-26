"""
Thin HTTP client for the hosted off-chain indexer (`indexer.api`).

Used by `HiveClient.discover_agents(prefer_indexer=True)` and the CLI to get
O(1) discovery over full history. Every method raises `IndexerUnavailable` on
any network/HTTP error so the caller can transparently fall back to the
on-chain event-scan — the indexer is a cache, never a hard dependency.
"""

from typing import Any, Dict, List, Optional

from mycelium_sdk.constants import INDEXER_URL


class IndexerUnavailable(Exception):
    """The hosted indexer could not be reached or returned an error."""


class IndexerClient:
    def __init__(self, base_url: Optional[str] = None, timeout: float = 5.0):
        self.base_url = (base_url or INDEXER_URL).rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        import requests

        try:
            resp = requests.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # noqa: BLE001 - any failure means fall back to chain
            raise IndexerUnavailable(f"indexer {self.base_url}{path}: {e}") from e

    def list_agents(
        self,
        capability: Optional[str] = None,
        min_reputation: int = 0,
        limit: int = 50,
        start_after: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit}
        if capability:
            params["capability"] = capability
        if min_reputation:
            params["min_reputation"] = min_reputation
        if start_after:
            params["start_after"] = start_after
        return self._get("/agents", params)

    def get_agent(self, name: str) -> Dict[str, Any]:
        return self._get(f"/agents/{name}")

    def list_jobs(self, **params: Any) -> Dict[str, Any]:
        return self._get("/jobs", {k: v for k, v in params.items() if v is not None})

    def stats(self) -> Dict[str, Any]:
        return self._get("/stats")
