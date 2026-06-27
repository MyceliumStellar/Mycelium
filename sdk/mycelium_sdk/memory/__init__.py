"""
Persistent agent memory — off-chain store + tiny on-chain commitment.

    from mycelium_sdk.memory import AgentMemory
    mem = AgentMemory(ctx)              # local backend by default

See `AgentMemory` for the developer API and `memory_anchor.py` for the on-chain
contract. Big mutable private data stays off-chain; only (root, uri, version)
goes on-chain, so per-agent on-chain cost is constant regardless of memory size.
"""

from mycelium_sdk.memory.agent_memory import AgentMemory, AnchoringPolicy
from mycelium_sdk.memory.anchor import MemoryAnchorClient
from mycelium_sdk.memory.backends import (
    LocalVectorBackend, SupermemoryBackend, TieredBackend,
)

__all__ = [
    "AgentMemory", "AnchoringPolicy", "MemoryAnchorClient",
    "LocalVectorBackend", "SupermemoryBackend", "TieredBackend",
]
