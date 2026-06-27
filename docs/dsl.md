# Mycelium DSL — Codebase Guide

The Mycelium DSL (`mycelium` package, published as `mycelium-dsl` on PyPI) is a
**zero-dependency Python package** that provides mock primitives, decorators, and
environmental fakes. It serves two purposes simultaneously:

1. **Developer-facing ergonomics**: contracts are valid, runnable Python — they
   lint, autocomplete, and pass `pytest` without any blockchain dependency.
2. **Compiler-facing metadata**: decorators and type annotations are AST markers
   that the compiler parses, validates, and transpiles to Soroban Rust.

Because the types are Python subclasses and the decorators are identity
functions, a contract file like [`escrow_contract.py`](file:///home/ansh/Mycelium/escrow_contract.py) is both a
compilable source **and** a runnable test fixture — `python escrow_contract.py`
exercises the contract logic locally.

---

## Package layout

```
mycelium/
├── __init__.py           # Re-exports everything from types.py
└── types.py              # 125 LOC: types, mocks, decorators
```

Source: [`mycelium/types.py`](file:///home/ansh/Mycelium/mycelium/types.py)

---

## Primitives & wrappers

Every DSL type inherits from a standard Python built-in so contracts remain
valid Python. The compiler's `map_type()` lowers each name to its Soroban Rust
equivalent.

| DSL class | Python base | Soroban Rust type | Notes |
|---|---|---|---|
| `Symbol` | `str` | `Symbol` | ≤ 32 chars, `[a-zA-Z0-9_]` |
| `Address` | `str` | `Address` | `G…` (ed25519) or `C…` (contract). Has `.require_auth()` |
| `Bytes` | `bytes` | `Bytes` | Raw byte sequence |
| `Bool` | `object` | `bool` | Boolean toggle |
| `i32` / `I32` | `int` | `i32` | Signed 32-bit |
| `u32` / `U32` | `int` | `u32` | Unsigned 32-bit |
| `i64` | `int` | `i64` | Signed 64-bit |
| `u64` / `U64` | `int` | `u64` | Unsigned 64-bit |
| `i128` / `I128` | `int` | `i128` | Signed 128-bit |
| `U128` | `int` | `u128` | Unsigned 128-bit |
| `Map` | `dict` | `Map<K, V>` | Key-value mapping |
| `Vec` | `list` | `Vec<T>` | Dynamic array |

All integer types are `int` subclasses. The SDK's `AgentContext._typed_int_scval`
recognizes these class names and marshals them to the correctly-typed SCVal
(`to_uint64`, `to_int128`, etc.), so `call_contract(..., [U64(40)])` uses `u64`
on-chain rather than the default `i128`.

---

## Decorators

Decorators are **identity functions** — they return the decorated target
unchanged. They serve as labels for the compiler's `MyceliumCompilerVisitor`
(see [compiler.md](./compiler.md)):

```python
@contract      # Mark a class as the contract struct → Rust #[contract]
@external      # Mark a method as a state-changing entry point → pub fn
@view          # Mark a method as read-only (simulation only) → pub fn
@storage       # Legacy annotation for storage actions
@event         # Mark a class as an event payload → #[contracttype] + emit
@auth          # Mark a function as requiring signature authentication
```

### Storage lifetime decorators

The `state` class provides static methods to declare which Soroban storage scope
a function operates in. The compiler uses these to generate the correct
`env.storage().instance()` / `.persistent()` / `.temporary()` calls:

```python
class state:
    @staticmethod
    def instance(func):   # Active as long as the contract instance exists
        return func

    @staticmethod
    def persistent(func): # Individual ledger entries; need rental renews
        return func

    @staticmethod
    def temporary(func):  # Ephemeral; can expire without renewal
        return func
```

---

## Environmental mocks

### `StorageMock`

Simulates a contract's ledger storage index. Backed by a plain `dict`:

| Method | Signature | Behavior |
|---|---|---|
| `get` | `(key, default=None) → Any` | Dict lookup with default |
| `set` | `(key, value) → None` | Dict write |
| `has` | `(key) → bool` | `key in dict` |
| `remove` | `(key) → None` | `del dict[key]` (if exists) |

### `Env`

Mock Soroban host environment. Every method returns a safe, testable default:

| Method | Returns | Soroban equivalent |
|---|---|---|
| `storage()` | `StorageMock()` | `env.storage().instance()` / `.persistent()` / `.temporary()` |
| `ledger()` → `timestamp()` | `0` (int) | `env.ledger().timestamp()` |
| `ledger()` → `sequence()` | `0` (int) | `env.ledger().sequence()` |
| `current_contract_address()` | empty `Address` | `env.current_contract_address()` |
| `current_contract()` | empty `Address` | (alias) |
| `call(contract, method, args)` | `None` | Cross-contract call |
| `invoke_contract(contract, method, args)` | `None` | Cross-contract invoke |
| `transfer(from, to, token, amount)` | `None` (no-op) | SEP-41 token transfer |
| `emit_event(topic, data)` | `None` (no-op) | Event emission |
| `crypto()` → `sha256(data)` | `Bytes(b"")` | `env.crypto().sha256()` |
| `crypto()` → `keccak256(data)` | `Bytes(b"")` | `env.crypto().keccak256()` |
| `crypto()` → `verify_sig_ed25519(pk, msg, sig)` | `True` | Ed25519 signature verification |

> The mock `sha256` returns empty bytes instead of computing a real hash. For
> production-accurate testing, use the real `hashlib.sha256` in your test
> fixtures (as the compiler tests do).

---

## How the compiler processes the DSL

When the compiler's `MyceliumCompilerVisitor` (see [compiler.md](./compiler.md))
parses a contract source file:

1. **Class definitions**: Locates classes carrying the `@contract` decorator.
   If no `@contract` class is found, top-level functions are collected as
   `ModuleContract` (Vyper-style).

2. **Type annotations**: Resolves types against the DSL primitives and the
   `map_type()` table. Both lowercase (`u64`, `i128`) and uppercase (`U64`,
   `I128`) forms are accepted.

3. **Storage access**: Replaces `self.storage.get("key")` /
   `self.storage.set("key", value)` with the corresponding Rust
   `env.storage().instance().get(...)` / `.set(...)`. The storage scope
   (`instance`, `persistent`, `temporary`) is chosen by the function's
   `@state.*` decorator.

4. **Environment injections**: Auto-prepends `env: Env` as the first argument
   in generated Rust functions. The Python `__init__` constructor is skipped
   (Soroban contracts have no Python-style constructor).

5. **Global pseudo-variables**: The compiler replaces Solidity-style globals
   with their Soroban equivalents:

   | Pseudo-variable | Lowered to |
   |---|---|
   | `msg_sender` | `Address` param + `require_auth()` |
   | `msg_value` | `U256` param |
   | `block_timestamp` | `env.ledger().timestamp()` |
   | `block_number` | `env.ledger().sequence() as u64` |
   | `ZERO_ADDRESS` | Canonical all-zero `Address` |

6. **Events**: `@event` classes become `#[contracttype]` structs with
   `env.events().publish()` calls.

7. **Errors**: A `ContractError` class with integer constants becomes a
   `#[contracterror] #[repr(u32)] enum ContractError`.

---

## Authoring a contract

### Class-based (Env-backed)

```python
from mycelium import contract, external, view, Address, U64, Env

@contract
class Counter:
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def increment(self) -> U64:
        count = self.storage.get("count", U64(0))
        self.storage.set("count", count + U64(1))
        return count + U64(1)

    @view
    def get_count(self) -> U64:
        return self.storage.get("count", U64(0))
```

### Module-based (Vyper-like)

```python
count: uint256
owner: address

@external
def increment():
    self.count = self.count + 1

@view
def get_count() -> uint256:
    return self.count
```

Both styles compile to identical WASM.

---

## Related docs

- [`compiler.md`](./compiler.md) — the compiler that parses and transpiles this DSL.
- [`contracts.md`](./contracts.md) — the production contracts authored in this DSL.
- [`sdk.md`](./sdk.md) — `AgentContext._typed_int_scval` that marshals DSL types to SCVals.
