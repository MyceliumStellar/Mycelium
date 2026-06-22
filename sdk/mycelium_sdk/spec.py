"""
Contract-spec fetching, caching, and spec-driven argument marshalling.

The DSL's typed-int wrappers (U64(40), u32(3), ...) exist so a Python int lands
at the right Soroban width — but they push that burden onto the caller. With the
contract's own spec in hand we can do better: fetch the `SCSpecFunctionV0` for a
function once, learn each parameter's declared type, and marshal plain Python
values to the exact width automatically. Then `ctx.call_contract(cid, "add",
[40])` "just works" without a U64() wrapper.

The spec is fetched from RPC via `SorobanServer.get_contract_spec` and cached per
(rpc_url, contract_id) for the process lifetime — it never changes for a deployed
contract id.
"""

from typing import Any, Dict, List, Optional, Tuple

# Process-wide cache: (rpc server url, contract id) -> ContractSpec.
_SPEC_CACHE: Dict[Tuple[str, str], Any] = {}


class SpecUnavailable(Exception):
    """Raised when a contract's spec cannot be fetched (e.g. RPC error)."""


def _cache_key(soroban_rpc, contract_id: str) -> Tuple[str, str]:
    url = getattr(soroban_rpc, "server_url", None) or str(id(soroban_rpc))
    return (str(url), contract_id)


def fetch_spec(soroban_rpc, contract_id: str, *, use_cache: bool = True):
    """
    Return the `ContractSpec` for `contract_id`, fetching from RPC on first use
    and caching thereafter. Raises SpecUnavailable on failure.
    """
    key = _cache_key(soroban_rpc, contract_id)
    if use_cache and key in _SPEC_CACHE:
        return _SPEC_CACHE[key]
    try:
        spec = soroban_rpc.get_contract_spec(contract_id)
    except Exception as exc:  # network / not-a-contract / unsupported
        raise SpecUnavailable(
            f"Could not fetch contract spec for {contract_id}: {exc}"
        ) from exc
    _SPEC_CACHE[key] = spec
    return spec


def clear_cache() -> None:
    """Drop all cached specs (mainly for tests)."""
    _SPEC_CACHE.clear()


def _symbol_to_str(name: Any) -> str:
    """Decode a function name, which may be an SCSymbol, bytes, or str."""
    if isinstance(name, str):
        return name
    if isinstance(name, (bytes, bytearray)):
        return name.decode("utf-8", errors="replace")
    # stellar_xdr.SCSymbol wraps the raw bytes on `.sc_symbol`.
    raw = getattr(name, "sc_symbol", None)
    if isinstance(raw, (bytes, bytearray)):
        return raw.decode("utf-8", errors="replace")
    return str(raw if raw is not None else name)


def function_names(spec) -> List[str]:
    """Return the names of every function declared in `spec` (decoded to str)."""
    return [_symbol_to_str(fn.name) for fn in spec.functions]


def fetch_function_names(soroban_rpc, contract_id: str) -> Optional[List[str]]:
    """Fetch the contract's declared function names, or None if the spec is unavailable."""
    try:
        return function_names(fetch_spec(soroban_rpc, contract_id))
    except SpecUnavailable:
        return None


def function_inputs(spec, function_name: str) -> Optional[List[Tuple[str, Any]]]:
    """
    Return [(arg_name, SCSpecTypeDef), ...] for `function_name`, or None if the
    function is not in the spec.
    """
    fn = spec.get_function(function_name)
    if fn is None:
        return None
    inputs = []
    for inp in fn.inputs:
        name = inp.name
        if isinstance(name, (bytes, bytearray)):
            name = name.decode("utf-8", errors="replace")
        inputs.append((name, inp.type))
    return inputs


# Map an SCSpecType integer-type kind -> the matching stellar_sdk.scval ctor.
def _int_ctor_for_type(type_kind) -> Optional[str]:
    from stellar_sdk import xdr

    return {
        xdr.SCSpecType.SC_SPEC_TYPE_U32: "to_uint32",
        xdr.SCSpecType.SC_SPEC_TYPE_I32: "to_int32",
        xdr.SCSpecType.SC_SPEC_TYPE_U64: "to_uint64",
        xdr.SCSpecType.SC_SPEC_TYPE_I64: "to_int64",
        xdr.SCSpecType.SC_SPEC_TYPE_TIMEPOINT: "to_timepoint",
        xdr.SCSpecType.SC_SPEC_TYPE_DURATION: "to_duration",
        xdr.SCSpecType.SC_SPEC_TYPE_U128: "to_uint128",
        xdr.SCSpecType.SC_SPEC_TYPE_I128: "to_int128",
        xdr.SCSpecType.SC_SPEC_TYPE_U256: "to_uint256",
        xdr.SCSpecType.SC_SPEC_TYPE_I256: "to_int256",
    }.get(type_kind)


def marshal_arg(value: Any, type_def, scval, to_scval_fallback):
    """
    Marshal a single Python `value` to an SCVal using its declared spec `type_def`.

    Only plain Python ints need the spec (to pick the right width); everything
    else (already-typed DSL wrappers, bytes, str, bool, lists, pre-built SCVals)
    is delegated to `to_scval_fallback`, preserving existing behaviour.
    """
    from stellar_sdk import xdr

    # An explicit DSL wrapper / pre-built SCVal / non-int already encodes intent —
    # don't second-guess the declared type, just use the default conversion.
    if type(value) is int:  # plain int, not a typed subclass or bool
        ctor = _int_ctor_for_type(type_def.type)
        if ctor is not None:
            return getattr(scval, ctor)(value)
    # bytes -> Bytes/BytesN, str -> String/Symbol/Address are handled fine by the
    # fallback; only integer width is ambiguous without the spec.
    return to_scval_fallback(value)


def marshal_args(
    soroban_rpc,
    contract_id: str,
    function_name: str,
    args: List[Any],
    scval,
    to_scval_fallback,
) -> List[Any]:
    """
    Marshal a positional `args` list for `function_name` using the contract spec.

    Falls back to per-value default conversion when the spec is unavailable or
    the function/arity is not described, so this never makes a call worse than
    the spec-less path.
    """
    try:
        spec = fetch_spec(soroban_rpc, contract_id)
        inputs = function_inputs(spec, function_name)
    except SpecUnavailable:
        inputs = None

    if not inputs or len(inputs) != len(args):
        # Unknown function or arity mismatch — let default conversion (and the
        # contract's own validation) handle it rather than guessing.
        return [to_scval_fallback(a) for a in args]

    return [
        marshal_arg(value, type_def, scval, to_scval_fallback)
        for value, (_, type_def) in zip(args, inputs)
    ]
