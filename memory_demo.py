"""
Persistent agent memory portability demo (testnet).

The pitch claim made concrete: an agent's memory is a big mutable off-chain
store, but only a tiny commitment (root, uri, version) lives on-chain — so the
SAME memory can be resumed on a different machine, and any tampering is caught.

Reusing the `testsdk` agent wallet (same one as `a2a_demo.py`), this shows:

  MACHINE A  write memories into a local store at dir A, publish the canonical
             blob to a shared file, and `anchor()` its root on-chain (1 tx).
  MACHINE B  a fresh, EMPTY local store at dir B (same wallet) → `rehydrate()`
             reads the on-chain anchor, fetches the published blob, re-hashes
             it, refuses to load on mismatch, then imports + recalls. State
             resumed across machines with nothing but the chain + the blob uri.
  TAMPER     flip a byte of the published blob → `rehydrate()`/`verify()` reject
             it (the on-chain root no longer matches).

This doubles as the portability proof for stateless / serverless agents: kill
the process, move hosts, rehydrate from the anchor, keep going.
"""

import os
import tempfile

from mycelium import AgentContext, AgentMemory

AGENT = {
    "wallet": "testsdk/.mycelium/wallet.json",
    "pass": "testsdk-pass-6465",
}


def main():
    ctx = AgentContext(AGENT["wallet"], network_type="testnet", passphrase=AGENT["pass"])

    workdir = tempfile.mkdtemp(prefix="mycelium-mem-demo-")
    dir_a = os.path.join(workdir, "machineA.db")
    dir_b = os.path.join(workdir, "machineB.db")
    blob_path = os.path.join(workdir, "memory.json")  # the "published" blob both machines share

    print(f"\n[demo] scratch dir: {workdir}")

    print("\n========== MACHINE A: write + publish + anchor ==========")
    mem_a = AgentMemory(ctx, backend="local", backend_kwargs={"path": dir_a})
    mem_a.remember("user prefers concise answers", tags=["pref"])
    mem_a.remember("project deadline is 2026-07-01", tags=["fact", "project"])
    mem_a.remember("avoid the deprecated v1 escrow initialize path", tags=["lesson"])
    print(f"wrote {mem_a.backend.count()} memories to machine A's local store")

    def publish(blob: bytes) -> str:
        with open(blob_path, "wb") as f:
            f.write(blob)
        return f"file://{blob_path}"

    version = mem_a.anchor(publish=publish)
    print(f"published blob -> file://{blob_path}")
    print(f"anchored on-chain: version={version}, root={mem_a.memory_root().hex()[:16]}…")
    assert mem_a.verify() is True, "machine A should match its own anchor"
    print("machine A verify(): in sync ✓")

    print("\n========== MACHINE B: fresh store → rehydrate from the anchor ==========")
    mem_b = AgentMemory(ctx, backend="local", backend_kwargs={"path": dir_b})
    assert mem_b.backend.count() == 0, "machine B starts empty"
    print(f"machine B local store starts empty ({mem_b.backend.count()} records)")
    out = mem_b.rehydrate()
    print(f"rehydrated {out['records']} record(s) from on-chain anchor version {out['version']}")
    assert mem_b.verify() is True, "machine B should now match the anchor"
    print("machine B verify(): in sync ✓")
    hits = mem_b.recall("when is the deadline", k=1)
    print(f"machine B recall('when is the deadline') -> {hits[0]['content']!r}")

    print("\n========== TAMPER: corrupt the published blob → rehydrate must reject ==========")
    with open(blob_path, "rb") as f:
        good = f.read()
    with open(blob_path, "wb") as f:
        f.write(good.replace(b"2026-07-01", b"2027-01-01"))  # silently move the deadline
    mem_c = AgentMemory(ctx, backend="local", backend_kwargs={"path": dir_b + ".tamper"})
    try:
        mem_c.rehydrate()
        print("✗ FAIL: tampered blob was accepted (should not happen)")
    except ValueError as exc:
        print(f"✓ tampered blob rejected: {exc}")

    print("\n[demo] done. Off-chain memory, on-chain trust — portable across machines.\n")


if __name__ == "__main__":
    main()
