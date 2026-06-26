"""Offline tests for persistent agent memory (local backend + anchor/verify)."""

import hashlib

import pytest

from mycelium_sdk.memory import AgentMemory, LocalVectorBackend, TieredBackend


class _FakeKP:
    public_key = "G" + "M" * 55


class _FakeContext:
    """Records set_anchor; serves a canned anchor for reads."""

    def __init__(self):
        self.keypair = _FakeKP()
        self.network_type = "testnet"
        self.calls = []
        self._anchor = {}      # owner -> {root, uri, acl, version}

    def call_contract(self, contract_id, function_name, args, read_only=False):
        self.calls.append((function_name, args, read_only))
        if function_name == "set_anchor":
            owner, root, uri, acl = args
            prev = self._anchor.get(owner, {}).get("version", 0)
            self._anchor[owner] = {
                "root": bytes(root), "uri": uri.decode() if isinstance(uri, bytes) else uri,
                "acl": bytes(acl), "version": prev + 1,
            }
            return type("Tx", (), {"return_value": prev + 1})()
        if function_name == "get_anchor":
            a = self._anchor.get(args[0])
            if not a:
                raise Exception("not anchored")
            return {"root": a["root"], "uri": a["uri"].encode(), "acl": a["acl"], "version": a["version"]}
        if function_name == "get_version":
            return self._anchor.get(args[0], {}).get("version", 0)
        return None


# ── local backend ───────────────────────────────────────────────────────────
def test_local_backend_remember_and_recall():
    b = LocalVectorBackend("Gowner", path=":memory:")
    b.remember("the sky is blue", ["fact"])
    b.remember("cats are mammals", ["fact", "bio"])
    b.remember("the ocean is also blue", ["fact"])
    hits = b.recall("what color is the sky", k=2)
    assert len(hits) == 2
    # the lexically-overlapping memories rank above the unrelated cat fact
    assert "cats are mammals" not in [h["content"] for h in hits]


def test_export_import_roundtrip_is_canonical():
    b1 = LocalVectorBackend("Gowner", path=":memory:")
    b1.remember("alpha", ["x"])
    b1.remember("beta", ["y", "z"])
    blob = b1.export_blob()

    b2 = LocalVectorBackend("Gowner", path=":memory:")
    n = b2.import_blob(blob)
    assert n == 2
    # re-export on the other instance produces byte-identical canonical blob
    assert b2.export_blob() == blob


# ── AgentMemory anchor / verify / rehydrate ──────────────────────────────────
def test_anchor_then_verify_true(tmp_path):
    ctx = _FakeContext()
    mem = AgentMemory(ctx, backend="local", backend_kwargs={"path": str(tmp_path / "a.db")})
    mem.remember("user prefers dark mode", ["pref"])
    version = mem.anchor(uri="file:///tmp/none")
    assert version == 1
    # set_anchor marshalled the real content root
    fn, args, _ = [c for c in ctx.calls if c[0] == "set_anchor"][0]
    assert bytes(args[1]) == mem.memory_root()
    assert mem.verify() is True


def test_verify_false_after_local_mutation(tmp_path):
    ctx = _FakeContext()
    mem = AgentMemory(ctx, backend="local", backend_kwargs={"path": str(tmp_path / "b.db")})
    mem.remember("fact one", [])
    mem.anchor(uri="x")
    mem.remember("fact two added after anchor", [])   # local now ahead of the commitment
    assert mem.verify() is False


def test_rehydrate_verifies_and_loads(tmp_path):
    # Machine A: write + anchor; publish the blob to a file.
    ctx_a = _FakeContext()
    mem_a = AgentMemory(ctx_a, backend="local", backend_kwargs={"path": str(tmp_path / "A.db")})
    mem_a.remember("portable memory", ["demo"])
    blob_path = tmp_path / "mem.json"
    blob_path.write_bytes(mem_a.backend.export_blob())
    mem_a.anchor(uri=f"file://{blob_path}")

    # Machine B: same wallet (shares the fake context's anchor store), empty store.
    mem_b = AgentMemory(ctx_a, backend="local", backend_kwargs={"path": str(tmp_path / "B.db")})
    assert mem_b.backend.count() == 0
    out = mem_b.rehydrate()
    assert out == {"version": 1, "records": 1}
    assert mem_b.backend.count() == 1
    assert mem_b.verify() is True


def test_tiered_backend_mirrors_writes_and_shares_root(tmp_path):
    # "use both": a local laptop cache + a (stand-in) second store at once.
    primary = LocalVectorBackend("Gowner", path=str(tmp_path / "primary.db"))
    secondary = LocalVectorBackend("Gowner", path=str(tmp_path / "secondary.db"))
    tiered = TieredBackend(primary, secondary)

    ctx = _FakeContext()
    mem = AgentMemory(ctx, backend=tiered)
    mem.remember("shared across both stores", ["x"])

    # write mirrored to both
    assert primary.count() == 1 and secondary.count() == 1
    # the on-chain root equals the standalone primary's root (interchangeable)
    assert mem.memory_root() == hashlib.sha256(primary.export_blob()).digest()
    mem.anchor(uri="cloud://supermemory/Gowner")
    assert mem.verify() is True


def test_rehydrate_rejects_tampered_blob(tmp_path):
    ctx = _FakeContext()
    mem = AgentMemory(ctx, backend="local", backend_kwargs={"path": str(tmp_path / "C.db")})
    mem.remember("real", [])
    mem.anchor(uri="ignored")
    # feed a blob that doesn't match the on-chain root
    with pytest.raises(ValueError):
        mem.rehydrate(fetch=lambda uri: b'{"owner":"x","records":[]}')
