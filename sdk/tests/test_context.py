"""Offline tests for AgentContext helpers (no network)."""

import json
import os

from stellar_sdk import Keypair, scval
from stellar_sdk import xdr as stellar_xdr

from mycelium_sdk import crypto
from mycelium_sdk.context import AgentContext


def _bare_context() -> AgentContext:
    """An AgentContext without RPC init, for testing pure helpers."""
    ctx = AgentContext.__new__(AgentContext)
    ctx._passphrase = "pw"
    return ctx


def _native(ctx, value):
    return scval.to_native(stellar_xdr.SCVal.from_xdr(ctx._to_scval(value).to_xdr()))


def test_to_scval_primitives():
    ctx = _bare_context()
    assert _native(ctx, True) is True
    assert _native(ctx, 123) == 123
    assert _native(ctx, b"\x01\x02") == b"\x01\x02"
    assert _native(ctx, "data_classifier_alpha") == "data_classifier_alpha"  # symbol
    assert _native(ctx, "https://x.sh/api") == "https://x.sh/api"            # string


def test_to_scval_address():
    ctx = _bare_context()
    kp = Keypair.random()
    decoded = _native(ctx, kp.public_key)
    assert getattr(decoded, "address", str(decoded)) == kp.public_key


def test_to_scval_passthrough():
    ctx = _bare_context()
    prebuilt = scval.to_uint64(7)
    assert ctx._to_scval(prebuilt) is prebuilt


def test_to_scval_typed_int_widths():
    """DSL typed-int wrappers must marshal to their declared Soroban width."""
    from mycelium import U64, U32, I128
    from mycelium_sdk import u64 as sdk_u64

    ctx = _bare_context()
    # The XDR discriminant differs per width; round-tripping the value is enough
    # to prove it was accepted as that type (a width mismatch would trap on-chain,
    # but here we assert the SCVal type tag is correct).
    assert ctx._to_scval(U64(40)).type == scval.to_uint64(40).type
    assert ctx._to_scval(U32(3)).type == scval.to_uint32(3).type
    assert ctx._to_scval(I128(9)).type == scval.to_int128(9).type
    # bare int still defaults to i128
    assert ctx._to_scval(5).type == scval.to_int128(5).type
    # SDK helper produces a real u64 SCVal
    assert sdk_u64(7).type == scval.to_uint64(7).type


def test_to_scval_non_address_56_char_string_is_not_address():
    """A 56-char non-key string must NOT be misclassified as an Address."""
    ctx = _bare_context()
    s = "G" + "1" * 55  # 56 chars, looks address-ish but is not a valid StrKey
    # Should fall through to symbol/string, not raise inside to_address.
    decoded = _native(ctx, s)
    assert decoded == s


def test_load_and_decrypt_keypair(tmp_path):
    kp = Keypair.random()
    wallet = {"public_key": kp.public_key, **crypto.encrypt_secret(kp.secret, "pw")}
    path = tmp_path / "wallet.json"
    path.write_text(json.dumps(wallet))

    ctx = _bare_context()
    loaded = ctx._load_and_decrypt_keypair(str(path))
    assert loaded.public_key == kp.public_key
