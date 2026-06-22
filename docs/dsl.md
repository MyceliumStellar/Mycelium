# Mycelium DSL Developer Internals Reference

This document covers the internal design of the Mycelium DSL framework (`mycelium` package, or `mycelium-dsl` distribution). It details how decorators, wrapper classes, mock ledger structures, and type maps allow Python-authored contracts to compile into standard Soroban Rust.

---

## 🧭 Design Goals

The Mycelium DSL serves two purposes:
1. **Developer-facing Ergonomics**: Allows smart contracts to be written using valid, strictly-typed, standard Python syntax, enabling linting, autocomplete, and local unit testing.
2. **Compiler-facing Metadata**: Exposes exact AST structures and annotations that the compiler parses, maps to Soroban types, and transpiles.

---

## 🗂️ Module Architecture (`mycelium/types.py`)

The codebase represents a zero-dependency package providing mock primitives and metadata classes:

```
mycelium/
├── __init__.py           # Re-exports types, decorators, and mocks
└── types.py              # Custom classes (int, str, dict, list subclasses)
```

---

## 🔡 Primitives & Wrappers

All DSL primitives inherit from standard Python built-ins. This ensures that contracts are valid Python and can execute locally during test simulations.

| DSL Class | Python Base | Soroban Mapping | Details |
|---|---|---|---|
| `Symbol` | `str` | `Symbol` | Validated for length $\le 32$ and charset `[a-zA-Z0-9_]` |
| `Address` | `str` | `Address` | Stellar public key (`G...`) or Contract Address (`C...`) |
| `Bytes` | `bytes` | `Bytes` | Raw byte sequence |
| `Bool` | `object` | `bool` | Boolean toggle |
| `i32` / `I32` | `int` | `i32` | Signed 32-bit integer |
| `u32` / `U32` | `int` | `u32` | Unsigned 32-bit integer |
| `i64` / `I64` | `int` | `i64` | Signed 64-bit integer |
| `u64` / `U64` | `int` | `u64` | Unsigned 64-bit integer |
| `i128` / `I128` | `int` | `i128` | Signed 128-bit integer |
| `U128` | `int` | `u128` | Unsigned 128-bit integer |
| `U256` / `uint256` | `int` | `U256` | Unsigned 256-bit big integer |
| `Map` | `dict` | `Map<K, V>` | Key-value associative mapping |
| `Vec` | `list` | `Vec<T>` | Dynamic index array |

---

## ⚡ Decorators & Namespaces

Decorators are defined as identity functions (returning the undecorated target). They serve as labels for the compiler's parser visitor:

```python
def contract(cls):
    """Marks a class boundary as a Smart Contract target."""
    return cls

def external(func):
    """Marks a method as an externally invocable smart contract action."""
    return func

def view(func):
    """Marks a method as a read-only (simulation) view call."""
    return func

def storage(func):
    """Legacy annotation for storage actions."""
    return func

def event(cls):
    """Marks a class as a structured contract Event payload."""
    return cls

def auth(func):
    """Marks a function as requiring cryptographic signature authentication."""
    return func
```

### State Storage Lifetime Decorators
The `state` class provides static methods to declare storage scopes:
```python
class state:
    @staticmethod
    def instance(func):
        """Instance storage: active as long as the contract instance exists."""
        return func
        
    @staticmethod
    def persistent(func):
        """Persistent storage: individual ledger entries requiring rental renews."""
        return func
        
    @staticmethod
    def temporary(func):
        """Temporary storage: ephemeral ledger entries that can expire."""
        return func
```

---

## 🧪 Environmental Mocks (`Env` & `StorageMock`)

To allow smart contracts to run within normal offline Python test environments (e.g., executing `escrow.py` locally without compiling it to WASM first), the DSL provides mock environments:

```python
class StorageMock:
    """Simulates a contract's ledger storage index."""
    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def has(self, key):
        return key in self._data

    def remove(self, key):
        if key in self._data:
            del self._data[key]

class Env:
    """Mock Env interface simulating Soroban host environment operations."""
    def storage(self):
        return StorageMock()

    def ledger(self):
        return self

    def timestamp(self):
        return int(time.time())

    def sequence(self):
        return 1

    def current_contract_address(self):
        return Address("CCMOCKCONTRACTADDRESS1234567890XYZ")

    def current_contract(self):
        return Address("CCMOCKCONTRACTADDRESS1234567890XYZ")

    def transfer(self, from_addr, to_addr, token, amount):
        # Simulates SEP-41 Token transfers
        pass

    def emit_event(self, topic, data):
        # Prints visual simulation output
        pass

    def crypto(self):
        return self

    def sha256(self, data):
        import hashlib
        return Bytes(hashlib.sha256(data).digest())
```

---

## 🎨 How the Compiler Processes the DSL

When the compiler's `MyceliumCompilerVisitor` (see [compiler.md](./compiler.md)) parses a source file:
1. **Class Defs**: Locates classes carrying the `@contract` decorator.
2. **Type Annotations**: Resolves types using the primitive/collection mappings.
3. **Storage Access**: Replaces `self.storage.get("key")` calls with `env.storage().instance().get(...)` or corresponding persistent/temporary Rust expressions depending on the annotated decorator context.
4. **Environment Injections**: Automatically prepends `env: Env` as the first argument in the generated Rust function implementations.
