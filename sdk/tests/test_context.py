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


def test_deploy_contract_pure_python(monkeypatch):
    """deploy_contract uploads the WASM hash then creates the contract, with no
    stellar-cli subprocess — both ops go through the Soroban RPC client."""
    from stellar_sdk import Network
    from stellar_sdk.soroban_rpc import GetTransactionStatus
    import mycelium_sdk.context as ctx_mod

    from stellar_sdk import StrKey

    kp = Keypair.random()
    contract_addr = StrKey.encode_contract(b"\x11" * 32)  # a valid C... StrKey
    appended = []  # records which op each built tx carried

    class _FakeBuilder:
        def __init__(self, *a, **k):
            pass

        def append_upload_contract_wasm_op(self, contract):
            appended.append(("upload", contract))
            return self

        def append_create_contract_op(self, wasm_id, address, salt=None):
            appended.append(("create", wasm_id, address))
            return self

        def set_timeout(self, _):
            return self

        def build(self):
            return object()

    class _Sim:
        error = None
        # create op return value is the new contract address
        results = [type("R", (), {"xdr": scval.to_address(contract_addr).to_xdr()})()]

    class _Send:
        hash = "txhash"

    class _Get:
        status = GetTransactionStatus.SUCCESS
        result_meta_xdr = None  # force fallback to the simulated return

    class _FakeRpc:
        def load_account(self, _pk):
            return object()

        def simulate_transaction(self, _tx):
            return _Sim()

        def prepare_transaction(self, tx):
            return type("Prepared", (), {"sign": lambda self, kp: None})()

        def send_transaction(self, _tx):
            return _Send()

        def get_transaction(self, _h):
            return _Get()

    monkeypatch.setattr(ctx_mod, "TransactionBuilder", _FakeBuilder, raising=False)
    # TransactionBuilder is imported inside _build_sign_submit; patch at source.
    import stellar_sdk
    monkeypatch.setattr(stellar_sdk, "TransactionBuilder", _FakeBuilder)
    # submit_transaction lives in rpc helpers; have it return our fake send.
    monkeypatch.setattr(ctx_mod.rpc_helpers, "submit_transaction", lambda rpc, tx, **k: _Send())

    cid = ctx_mod.deploy_contract(
        _FakeRpc(), kp, Network.TESTNET_NETWORK_PASSPHRASE, b"\x00asm\x01\x02\x03"
    )

    assert cid == contract_addr
    assert appended[0][0] == "upload" and appended[0][1] == b"\x00asm\x01\x02\x03"
    assert appended[1][0] == "create"
    # create references the SHA-256 of the WASM bytes as the wasm id
    import hashlib
    assert appended[1][1] == hashlib.sha256(b"\x00asm\x01\x02\x03").digest()


def test_load_and_decrypt_keypair(tmp_path):
    kp = Keypair.random()
    wallet = {"public_key": kp.public_key, **crypto.encrypt_secret(kp.secret, "pw")}
    path = tmp_path / "wallet.json"
    path.write_text(json.dumps(wallet))

    ctx = _bare_context()
    loaded = ctx._load_and_decrypt_keypair(str(path))
    assert loaded.public_key == kp.public_key
