"""
Width-correct Soroban value helpers.

A Soroban contract parameter has an exact type (`u64`, `u32`, `i128`, `Address`,
`Bytes`, ...). A bare Python `int` carries no width, so `AgentContext` marshals
it as `i128` by default — which traps if the contract expects `u64`/`u32`. These
helpers let user code declare the intended type WITHOUT importing `stellar_sdk`:

    from mycelium import u64, address
    ctx.call_contract(cid, "add", [u64(40)])
    ctx.call_contract(cid, "pay", [address(dest), u64(amount)])

Each returns a `stellar_sdk.xdr.SCVal`, which `AgentContext._to_scval` passes
through untouched.
"""

from typing import Any


def _scval():
    from stellar_sdk import scval
    return scval


def u32(n: int) -> Any:
    """Unsigned 32-bit integer SCVal."""
    return _scval().to_uint32(int(n))


def u64(n: int) -> Any:
    """Unsigned 64-bit integer SCVal."""
    return _scval().to_uint64(int(n))


def u128(n: int) -> Any:
    """Unsigned 128-bit integer SCVal."""
    return _scval().to_uint128(int(n))


def i32(n: int) -> Any:
    """Signed 32-bit integer SCVal."""
    return _scval().to_int32(int(n))


def i64(n: int) -> Any:
    """Signed 64-bit integer SCVal."""
    return _scval().to_int64(int(n))


def i128(n: int) -> Any:
    """Signed 128-bit integer SCVal."""
    return _scval().to_int128(int(n))


def address(addr: str) -> Any:
    """Account (G...) or contract (C...) address SCVal."""
    return _scval().to_address(addr)


def symbol(s: str) -> Any:
    """Soroban Symbol SCVal (<=32 chars, [a-zA-Z0-9_])."""
    return _scval().to_symbol(s)


def string(s: str) -> Any:
    """Soroban String SCVal (arbitrary text)."""
    return _scval().to_string(s)


def bytes_val(b: bytes) -> Any:
    """Soroban Bytes SCVal."""
    return _scval().to_bytes(bytes(b))


def boolean(b: bool) -> Any:
    """Soroban Bool SCVal."""
    return _scval().to_bool(bool(b))
