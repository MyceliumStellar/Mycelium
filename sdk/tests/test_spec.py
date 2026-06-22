"""Offline tests for spec-driven argument marshalling."""

from stellar_sdk import scval
from stellar_sdk import xdr

from mycelium_sdk import spec as sp


class _FakeInput:
    def __init__(self, name, type_kind):
        self.name = name
        self.type = xdr.SCSpecTypeDef(type_kind)


class _FakeFn:
    def __init__(self, inputs):
        self.inputs = inputs


class _FakeSpec:
    def __init__(self, fns):
        self._fns = fns

    def get_function(self, name):
        return self._fns.get(name)


def _default_to_scval(value):
    # Mirrors AgentContext's fallback: a plain int becomes i128.
    if isinstance(value, int) and not isinstance(value, bool):
        return scval.to_int128(value)
    raise AssertionError("fallback should only see plain ints in these tests")


def test_marshal_plain_int_to_declared_u64():
    type_def = xdr.SCSpecTypeDef(xdr.SCSpecType.SC_SPEC_TYPE_U64)
    out = sp.marshal_arg(40, type_def, scval, _default_to_scval)
    assert out == scval.to_uint64(40)


def test_marshal_plain_int_to_declared_u32():
    type_def = xdr.SCSpecTypeDef(xdr.SCSpecType.SC_SPEC_TYPE_U32)
    out = sp.marshal_arg(3, type_def, scval, _default_to_scval)
    assert out == scval.to_uint32(3)


def test_marshal_falls_back_for_non_int_type():
    # A symbol-typed param: integer marshalling doesn't apply, fallback is used.
    type_def = xdr.SCSpecTypeDef(xdr.SCSpecType.SC_SPEC_TYPE_SYMBOL)
    # value is an int here only to exercise the fallback branch deterministically
    out = sp.marshal_arg(7, type_def, scval, _default_to_scval)
    assert out == scval.to_int128(7)  # fallback path


def test_marshal_args_uses_spec_widths():
    spec = _FakeSpec(
        {"add": _FakeFn([_FakeInput("amount", xdr.SCSpecType.SC_SPEC_TYPE_U64)])}
    )

    class _Rpc:
        server_url = "memory://test"

        def get_contract_spec(self, cid):
            return spec

    sp.clear_cache()
    out = sp.marshal_args(_Rpc(), "C123", "add", [40], scval, _default_to_scval)
    assert out == [scval.to_uint64(40)]


def test_marshal_args_falls_back_on_arity_mismatch():
    spec = _FakeSpec(
        {"add": _FakeFn([_FakeInput("amount", xdr.SCSpecType.SC_SPEC_TYPE_U64)])}
    )

    class _Rpc:
        server_url = "memory://test2"

        def get_contract_spec(self, cid):
            return spec

    sp.clear_cache()
    # Two args for a one-arg function -> default conversion for each.
    out = sp.marshal_args(_Rpc(), "C123", "add", [1, 2], scval, _default_to_scval)
    assert out == [scval.to_int128(1), scval.to_int128(2)]


def test_fetch_spec_is_cached():
    calls = []

    class _Rpc:
        server_url = "memory://cache"

        def get_contract_spec(self, cid):
            calls.append(cid)
            return _FakeSpec({})

    sp.clear_cache()
    rpc = _Rpc()
    sp.fetch_spec(rpc, "Cabc")
    sp.fetch_spec(rpc, "Cabc")
    assert calls == ["Cabc"]  # second call served from cache
