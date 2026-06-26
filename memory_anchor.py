"""
MemoryAnchor — the tiny on-chain commitment for an agent's off-chain memory.

Agent memory (conversation logs, embeddings, documents) is large, mutable, and
often private, so it lives OFF-chain (see `sdk/mycelium_sdk/memory/`). The chain
holds only a constant, tiny commitment per agent:

  - root    : SHA-256 (or Merkle) root of the committed memory state (Bytes)
  - uri      : where to fetch the memory blob — Supermemory container / IPFS / https (Bytes)
  - acl      : who may read/write (addresses or capability), opaque to the chain (Bytes)
  - version  : monotonic counter — defines "latest", prevents rollback/replay (U64)

This makes memory portable (rehydrate on any machine from the anchor),
verifiable (recompute the root and compare), and access-controlled (only the
owner may update, enforced by `require_auth`). The on-chain footprint is a few
hundred bytes regardless of how much the agent remembers.

Authored in the Mycelium DSL and compiled with this repo's own compiler:

    python -m mycelium_compiler.main memory_anchor.py -o build/memory_anchor.wasm

Deploy once per network and record the contract id. The `memory_anchored` event
is consumed by the off-chain indexer for an O(1) "latest anchor per agent"
lookup. Reuses only v0.1.0 DSL primitives — no compiler changes.
"""

from mycelium import (
    contract, external, view,
    Address, U64, Bytes, Bool, Map, Env, Symbol,
)


class ContractError:
    NOT_ANCHORED = 1


@contract
class MemoryAnchor:
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def set_anchor(self, owner: Address, memory_root: Bytes, uri: Bytes, acl: Bytes) -> U64:
        """
        Commit the agent's off-chain memory state. Stores the root hash, fetch
        URI, and ACL, bumping a monotonic `version`. Only the owner may anchor
        its own memory (`require_auth`). Returns the new version.
        """
        owner.require_auth()

        version = self.storage.get("ver:" + str(owner), U64(0)) + U64(1)
        self.storage.set("root:" + str(owner), memory_root)
        self.storage.set("uri:" + str(owner), uri)
        self.storage.set("acl:" + str(owner), acl)
        self.storage.set("ver:" + str(owner), version)
        self.storage.set("has:" + str(owner), True)

        self.env.emit_event("memory_anchored", {"owner": owner, "version": version})
        return version

    @view
    def get_anchor(self, owner: Address) -> Map:
        """Return the agent's current anchor (root, uri, acl, version). Reverts if never anchored."""
        if not self.storage.get("has:" + str(owner), False):
            raise ContractError.NOT_ANCHORED

        details = Map()
        details.set(Symbol("root"), self.storage.get("root:" + str(owner)))
        details.set(Symbol("uri"), self.storage.get("uri:" + str(owner)))
        details.set(Symbol("acl"), self.storage.get("acl:" + str(owner)))
        details.set(Symbol("version"), self.storage.get("ver:" + str(owner)))
        return details

    @view
    def get_version(self, owner: Address) -> U64:
        """Return the agent's current anchor version (0 if never anchored)."""
        return self.storage.get("ver:" + str(owner), U64(0))

    @view
    def is_anchored(self, owner: Address) -> Bool:
        """Return whether `owner` has ever anchored memory."""
        return self.storage.get("has:" + str(owner), False)
