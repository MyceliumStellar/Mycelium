"""
Typed contract clients — `ctx.contract(cid).add(40)`.

Instead of `ctx.call_contract(cid, "add", [40])` everywhere, a `ContractClient`
turns a deployed contract's functions into native-looking methods, discovered
from the contract's own on-chain spec (so a typo or wrong arity is caught as an
`AttributeError`, and `dir(client)` lists the real functions).

    client = ctx.contract(cid)
    client.add(40)                 # signs + submits a state-changing tx
    client.read.get_count()        # simulates a view (no fee, no signature)
    await client.aio.add(40)       # async (state-changing)
    await client.aio.read.get_count()

Args are spec-marshalled and calls flow through `AgentContext.call_contract` /
`acall_contract`, so retry/backoff, dry-run, and width-correct marshalling all
apply unchanged. If the spec can't be fetched, method names aren't validated
(any name is allowed) so the client still works against the contract's own
validation.
"""

from typing import Any, List, Optional

from mycelium_sdk import spec as spec_mod


class _Methods:
    """A callable namespace whose attribute lookups become contract calls.

    `read_only` selects simulate vs submit; `is_async` selects
    `acall_contract` (returns a coroutine) vs `call_contract`.
    """

    def __init__(self, client: "ContractClient", *, read_only: bool, is_async: bool):
        # Set via __dict__ so __getattr__ never sees these as missing.
        self.__dict__["_client"] = client
        self.__dict__["_read_only"] = read_only
        self.__dict__["_is_async"] = is_async

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        client = self._client
        client._validate(name)

        def invoke(*args: Any):
            ctx, cid, ro = client._ctx, client._cid, self._read_only
            if self._is_async:
                return ctx.acall_contract(cid, name, list(args), read_only=ro)
            return ctx.call_contract(cid, name, list(args), read_only=ro)

        invoke.__name__ = name
        return invoke

    def __dir__(self):
        names = self._client._function_names()
        return list(names) if names is not None else super().__dir__()


class _AsyncNamespace:
    """`client.aio` — async siblings of the sync methods, plus `.read`."""

    def __init__(self, client: "ContractClient"):
        self.__dict__["_write"] = _Methods(client, read_only=False, is_async=True)
        self.__dict__["read"] = _Methods(client, read_only=True, is_async=True)

    def __getattr__(self, name: str):
        return getattr(self._write, name)

    def __dir__(self):
        return dir(self._write)


class ContractClient:
    """A spec-driven client for a single deployed contract (see module docstring)."""

    def __init__(self, context, contract_id: str):
        self._ctx = context
        self._cid = contract_id
        self._names_cache: Optional[List[str]] = None
        self._names_fetched = False
        # State-changing calls dispatch through this client directly; read-only
        # via `.read`; async via `.aio`.
        self._write = _Methods(self, read_only=False, is_async=False)
        self.read = _Methods(self, read_only=True, is_async=False)
        self.aio = _AsyncNamespace(self)

    @property
    def contract_id(self) -> str:
        return self._cid

    def _function_names(self) -> Optional[List[str]]:
        """Cached list of the contract's function names (None if spec unavailable)."""
        if not self._names_fetched:
            self._names_fetched = True
            try:
                self._names_cache = spec_mod.fetch_function_names(
                    self._ctx.soroban_rpc, self._cid
                )
            except Exception:
                self._names_cache = None
        return self._names_cache

    def _validate(self, name: str) -> None:
        """Raise AttributeError for a name the spec says isn't a contract function."""
        names = self._function_names()
        if names is not None and name not in names:
            raise AttributeError(
                f"Contract {self._cid} has no function '{name}'. "
                f"Available: {', '.join(sorted(names)) or '(none)'}"
            )

    def __getattr__(self, name: str):
        # Only reached for names not set in __init__ — treat as a contract call.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self.__dict__["_write"], name)

    def __dir__(self):
        names = self._function_names()
        base = ["read", "aio", "contract_id"]
        return base + (list(names) if names is not None else [])
