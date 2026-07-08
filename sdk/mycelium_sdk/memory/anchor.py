"""
MemoryAnchorClient — thin wrapper over the on-chain MemoryAnchor contract
(`memory_anchor.py`). Stores/reads the tiny per-agent commitment
(root, uri, acl, version). Real Soroban calls via `AgentContext`; no mocks.
"""

from typing import Any, Dict, Optional


def _addr_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    return getattr(value, "address", None) or str(value)


def _to_bytes(value: Any) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8")
    return b"" if value is None else bytes(value)


class MemoryAnchorClient:
    def __init__(self, context, anchor_address: Optional[str] = None):
        from mycelium_sdk.constants import contract_address

        self.context = context
        self.anchor_address = anchor_address or contract_address("memory_anchor", getattr(self.context, "network_type", "testnet"))

    def set_anchor(self, memory_root: bytes, uri: str, acl: bytes = b"") -> int:
        """
        Commit `memory_root` + `uri` (+ optional `acl`) on-chain for the wallet's
        owner address, bumping the monotonic version. Returns the new version.
        """
        result = self.context.call_contract(
            contract_id=self.anchor_address,
            function_name="set_anchor",
            args=[
                self.context.keypair.public_key,
                bytes(memory_root),
                uri.encode("utf-8") if isinstance(uri, str) else bytes(uri),
                bytes(acl),
            ],
        )
        version = getattr(result, "return_value", result)
        return int(version) if version is not None else 0

    def get_anchor(self, owner: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Return the anchor {root, uri, acl, version} for `owner` (default: wallet),
        or None if the agent has never anchored. Real RPC errors propagate (we
        detect "never anchored" via the non-panicking version, so None is
        unambiguous rather than swallowing transient failures).
        """
        owner = owner or self.context.keypair.public_key
        if self.get_version(owner) == 0:
            return None
        raw = self.context.call_contract(
            contract_id=self.anchor_address,
            function_name="get_anchor",
            args=[owner],
            read_only=True,
        )
        if not isinstance(raw, dict):
            return None
        return {
            "root": _to_bytes(raw.get("root")),
            "uri": _to_bytes(raw.get("uri")).decode("utf-8", "replace"),
            "acl": _to_bytes(raw.get("acl")),
            "version": int(raw.get("version") or 0),
        }

    def get_version(self, owner: Optional[str] = None) -> int:
        """Return the current anchor version for `owner` (0 if never anchored)."""
        owner = owner or self.context.keypair.public_key
        raw = self.context.call_contract(
            contract_id=self.anchor_address,
            function_name="get_version",
            args=[owner],
            read_only=True,
        )
        return int(raw or 0)
