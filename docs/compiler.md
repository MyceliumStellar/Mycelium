# Mycelium Compiler Architecture Guide

The Mycelium compiler turns a **Python smart contract** into a deployable
**Soroban WebAssembly** binary. It does this by transpiling a validated Python
AST into idiomatic Soroban Rust, then driving the official `stellar contract
build` toolchain to produce a `wasm32v1-none` `.wasm`.

This document covers the compilation pipeline, every module in the package, the
Python→Rust type system, the reproducible Docker image, and how to run and
benchmark the compiler.

---

## 🧭 Pipeline Overview

```
  contract.py
      │
      ▼
┌───────────────┐   ast.parse + MyceliumCompilerVisitor
│   parser.py   │   → contract name, state vars, functions, events,
└───────────────┘     structs, interfaces, errors, const-classes, constants
      │  (MyceliumCompilerVisitor)
      ▼
┌───────────────┐   type-checks every state var, arg, and return against
│  validator.py │   the Soroban-supported primitive/container set
└───────────────┘
      │
      ▼
┌────────────────────────── codegen/ ──────────────────────────┐
│  inferrer.py   StorageTypeInferrer — infers storage key/value │
│                and local-variable types across all functions  │
│  transpiler.py RustTranspiler — lowers each statement/expr    │
│                of the Python AST to Soroban Rust              │
│  core.py       generate_rust_intermediate → lib.rs            │
│                generate_wasm → stellar contract build → .wasm │
│  utils.py      type mapping, keyword escaping, helpers        │
└───────────────────────────────────────────────────────────────┘
      │
      ▼
  target.wasm   (wasm32v1-none, release, opt-level "z")
```

The driver in `mycelium_compiler/main.py` wires the three stages together:

```python
visitor    = parse_source(source_code)   # parser.py
validate_ast(visitor)                    # validator.py
wasm_bytes = generate_wasm(visitor)      # codegen/core.py
```

---

## 📁 Package Layout

```
compiler/
├── Dockerfile                       # Reproducible, machine-independent build image
├── requirements.txt                 # pytest (the compiler itself is stdlib-only)
├── mycelium_compiler/
│   ├── main.py                      # CLI entrypoint: compile_file(src, out)
│   ├── parser.py                    # AST → MyceliumCompilerVisitor (IR)
│   ├── validator.py                 # Type validation against Soroban primitives
│   ├── types.py                     # (reserved)
│   └── codegen/
│       ├── __init__.py              # Re-exports the public codegen API
│       ├── core.py                  # Rust emission + stellar build driver
│       ├── transpiler.py            # RustTranspiler (statement/expression lowering)
│       ├── inferrer.py              # StorageTypeInferrer (whole-contract type inference)
│       └── utils.py                 # map_type, escape_keyword, AST helpers
├── scripts/
│   ├── compile_batch.py             # Bulk-compile dirs of contracts (runs in-image)
│   ├── build_all_contracts.py       # Compile contracts/contracts/ one-by-one
│   └── run_stress_tests.py          # Stress harness
├── tests/
│   └── test_compiler.py             # pytest suite
└── Benchmark/contracts/             # Reference contract fixtures
```

---

## 1. Parser — `parser.py`

`parse_source(source_code)` runs `ast.parse` and walks the tree with
`MyceliumCompilerVisitor`, producing the compiler's intermediate representation.
The visitor supports **two authoring styles**:

| Style | Trigger | Example |
|-------|---------|---------|
| **Class-based** | a `class` decorated with `@contract` | `@contract\nclass Token: ...` |
| **Module-based** (Vyper-like) | no `@contract` class; top-level vars + functions | bare `balance: U256` + `def transfer(...)` |

In module mode the contract is named `ModuleContract`, and top-level functions
are included if they are undecorated or carry `@external` / `@view` /
`@public` / `@internal`.

### What the visitor collects

| Field | Source | Purpose |
|-------|--------|---------|
| `contract_name` | `@contract` class name (or `ModuleContract`) | Rust struct name |
| `state_variables` | `AnnAssign` at class/module top level | storage schema (each defaults to `instance` storage) |
| `functions` | `FunctionDef` | name, typed args, return type, storage mode, AST node |
| `events` | `@event` classes | event field schemas |
| `interfaces` | `@interface` classes | external-contract call surfaces |
| `structs` | plain classes with typed fields | custom value types |
| `errors` | a `ContractError` class | `name → u32` map → `#[contracterror]` enum |
| `const_classes` | classes of `NAME = <int constant>` | enum / constant groups |
| `module_constants` | module-level `NAME = <expr>` | compile-time constants |

`module_constants` are folded at parse time by `_eval_static_constant`, a small
safe evaluator that handles literals, unary/binary arithmetic, and the type
constructor wrappers (`U64(...)`, `U128(...)`, `Symbol(...)`, …). This lets a
contract write `TOTAL = 1_000 * 10 ** 6` and have it baked in as a constant.

---

## 2. Validator — `validator.py`

`validate_ast(visitor)` checks that **every state variable, function argument,
and return type** maps to a Soroban-supported type, raising `TypeError`
otherwise. `is_valid_type` accepts:

- **Primitives** — `U256`/`uint256`, `Address`, `U128`/`U64`/`U32`, `I128`/`I32`,
  `Bool`, `Symbol`/`String`, `Bytes`, `bytes32`, plus Python aliases (`int`,
  `str`, `bool`, `address`, …).
- **Containers** — `Map[K, V]` / `Mapping[K, V]` / `dict[K, V]`,
  `Vec[T]` / `List[T]` / `list[T]`, `DynArray[T, N]`, sized `Bytes[N]`, tuples
  `(A, B)`.
- **Custom types** — any parsed `struct` or `interface`.
- **Wrappers** — `constant(...)`, `indexed(...)`, and the `T or None` optional
  suffix (validated against the inner type).

Validation is intentionally structural (string-shape based), mirroring the way
`map_type` lowers the same shapes in codegen.

---

## 3. Codegen — `codegen/`

### 3.1 Type inference — `inferrer.py`

`StorageTypeInferrer` runs **once across the whole contract** before any Rust is
emitted. It reconciles:

- **Storage key/value types** — what `self.storage.get(key)` / `set(key, value)`
  read and write, so map and instance keys get concrete Rust types.
- **Function-local variable types** — propagated from assignments, arguments,
  storage reads, and arithmetic.

A subtlety captured by `NoneComparisonCollector` (in `transpiler.py`): a
storage read becomes an `Option<T>` **only** when the value is actually tested
for `None` (`x is None`, `not x`, `if x:`). A `get(key, <default>)` with a
concrete default lowers to `.unwrap_or(default)` and stays a plain value. This
is the single most important rule for avoiding mass regressions — over-eagerly
marking reads as `Option<T>` breaks every contract that uses the value
directly.

### 3.2 Statement/expression lowering — `transpiler.py`

`RustTranspiler` (the largest module, ~2000 lines) is an `ast.NodeVisitor` that
emits Soroban Rust for each Python construct: assignments, `if`/`while`/`for`
loops, comparisons, arithmetic, storage access, event emission, external calls,
errors/reverts, and the Mycelium "global" pseudo-variables. It carries the
inferred type tables so each emitted expression is correctly typed and
turbofished where Rust requires it.

### 3.3 Rust emission + build driver — `core.py`

**`generate_rust_intermediate(visitor)`** assembles the full `lib.rs`:

1. `#![no_std]` + a broad `use soroban_sdk::{...}` (and `contracterror`,
   `panic_with_error` when the contract declares errors).
2. A `#[contracterror] #[repr(u32)] enum ContractError` if errors are present.
3. `#[repr(u32)]` enums for integer `const_classes`.
4. `#[contract] pub struct <Name>;` and `#[contractimpl] impl <Name> { … }`.
5. One Rust `fn` per contract function, with:
   - visibility (`pub` for `@external`/`@view` and non-underscore names),
   - `env: Env` auto-injected first, then mapped user args,
   - **global emulation bindings** injected on demand (Solidity-style ergonomics):

     | Pseudo-variable | Lowered to |
     |-----------------|-----------|
     | `msg_sender` | `Address` param + `msg_sender.require_auth();` |
     | `msg_value` | `U256` param |
     | `block_timestamp` | `env.ledger().timestamp()` |
     | `block_number` | `env.ledger().sequence() as u64` |
     | `ZERO_ADDRESS` | the canonical all-zero `Address` |
     | `self_balance` | a `U256` stand-in |
   - a `let mut` declaration prelude generated **after** the body is transpiled,
     so type mutations discovered during lowering are reflected, and
   - the `__init__` constructor skipped (Soroban contracts have no Python ctor).

**`generate_wasm(visitor)`** then:

1. Calls `generate_rust_intermediate` and writes `src/lib.rs`.
2. Writes a `Cargo.toml` pinned to **`soroban-sdk = "26.1.0"`** with a release
   profile tuned for size and safety:
   `opt-level = "z"`, `overflow-checks = true`, `lto = true`,
   `codegen-units = 1`, `panic = "abort"`.
3. Resolves the `stellar` binary via `ensure_stellar_cli()` and runs
   `stellar contract build --manifest-path Cargo.toml`.
4. Reads back `target/wasm32v1-none/release/mycelium_contract.wasm` (falling
   back to `wasm32-unknown-unknown`).

**Workspace reuse & offline builds.** If `/app/mycelium_contract_workspace`
exists (the Docker image's pre-warmed crate), it is reused as the build dir and
`CARGO_TARGET_DIR=/app/cargo_target` + `CARGO_NET_OFFLINE=true` make the build
fully offline and fast. Off-Docker, a fresh temp workspace is created per
compile and cleaned up in a `finally` block.

> ⚠️ **Version lock:** the `soroban-sdk` version must be **identical** in both
> the Dockerfile pre-warm `Cargo.toml` and `core.py`'s `generate_wasm`
> `Cargo.toml`. If they drift, the offline runtime build cannot find the cached
> dependencies and every compile fails.

### 3.4 Helpers — `utils.py`

- **`map_type(t)`** — the canonical Python→Rust type lowering (table below).
- **`escape_keyword(name)`** — prefixes Rust reserved words with `r#`.
- **`to_pascal_case(name)`** — `NOT_INITIALIZED` → `NotInitialized` for enums.
- **`flatten_subscript` / `get_subscript_type`** — resolve nested
  `self.map[a][b]` accesses to a storage key and value type.
- **`check_keyword_usage`** — detects use of the global pseudo-variables.

---

## 🔤 Python → Rust / Soroban Type Mapping

| Mycelium / Python type | Soroban Rust type |
|------------------------|-------------------|
| `int`, `uint256`, `u256`, `U256` | `U256` |
| `U128` | `u128` |
| `U64` | `u64` |
| `U32` | `u32` |
| `I128` | `i128` |
| `I32` | `i32` |
| `bool`, `Bool` | `bool` |
| `str`, `String`, `Symbol` | `Symbol` |
| `address`, `Address` | `Address` |
| `bytes32` | `soroban_sdk::BytesN<32>` |
| `Bytes`, `Bytes[N]` | `soroban_sdk::Bytes` |
| `Map[K, V]`, `Mapping[K, V]`, `dict[K, V]` | `soroban_sdk::Map<K, V>` |
| `Vec[T]`, `List[T]`, `list[T]`, `DynArray[T, N]` | `soroban_sdk::Vec<T>` |
| `list` (bare) | `soroban_sdk::Vec<Val>` |
| `tuple` / `(A, B)` | `(Val, Val)` / `(A, B)` |
| `T or None` | `Option<T>` |

Element/key/value types recurse through `map_type`, so `Map[Address, Vec[U64]]`
becomes `Map<Address, Vec<u64>>`.

---

## 🐳 Reproducible Docker Image (god-level setup)

The compiler ships as a **self-contained, machine-independent image**,
`mycelium-compiler:latest` (~1.6 GB). It bundles the exact Rust toolchain,
`stellar-cli`, WASM targets, and a pre-warmed crate so contract compiles are
deterministic and run fully offline.

### Pinned toolchain

| Component | Version | Why it's pinned |
|-----------|---------|-----------------|
| Base image | `rust:1.95-slim-bookworm` | rustc/cargo baseline |
| `stellar-cli` | **27.0.0** | drives `contract build`; binary is downloaded & verified |
| WASM target | `wasm32v1-none` | the target stellar-cli 25.x+ builds for |
| `soroban-sdk` | **26.1.0** | contract dependency; raised the contract-fn param limit (>10) |

> The slim base lacks `libdbus-1-3 libudev1 libssl3`, which the `stellar`
> binary is dynamically linked against — without them `stellar` fails with
> **exit 127**. The Dockerfile installs them explicitly.

### What the Dockerfile does (`compiler/Dockerfile`)

1. **Base + memory guard** — `FROM rust:1.95-slim-bookworm`, with
   `CARGO_BUILD_JOBS=1` to keep peak memory low on small machines.
2. **System libs** — installs `curl`, `git`, `ca-certificates`, `python3`, and
   the three runtime libs above.
3. **WASM targets** — `rustup target add wasm32v1-none wasm32-unknown-unknown`.
4. **stellar-cli** — downloads the pinned `v27.0.0` Linux tarball, extracts to
   `/usr/local/bin`, and asserts `stellar --version`.
5. **Pre-warm cache** — builds a dummy `#![no_std]` soroban contract in
   `/app/mycelium_contract_workspace` with `CARGO_TARGET_DIR=/app/cargo_target`.
   This caches `soroban-sdk` and all transitive crates so later compiles run
   **fully offline**. (This is why the SDK version must match `core.py`.)
6. **Copy sources** — `compiler/`, `mycelium/`, `sdk/`, and sets
   `PYTHONPATH=/app/compiler:/app/sdk:.`.
7. **Entrypoint** — compiles a single contract mounted at
   `/workspace/contract.py` to `/workspace/target.wasm`.

### Build the image

The build context is the **repo root** (`.dockerignore` excludes
`venv/`, `.git/`, `ide/`, `contracts/`, etc. to keep the context small):

```bash
docker build -f compiler/Dockerfile -t mycelium-compiler:latest .
```

### Compile one contract

```bash
docker run --rm \
  -v "$PWD/my_contract.py:/workspace/contract.py:ro" \
  -v "/tmp/out:/workspace" \
  mycelium-compiler:latest
# → /tmp/out/target.wasm
```

The IDE backend invokes the image with a hardened sandbox profile
(`--network none --memory 512m --cpus 1.0 --rm`, 30s timeout) — see
[ide.md](./ide.md#-compilation-sandbox).

### Bulk-compile / benchmark

Override the entrypoint to run `scripts/compile_batch.py` against one or more
mounted directories:

```bash
docker run --rm \
  -v "$PWD/contracts/contracts:/work/normal:ro" \
  -v "$PWD/mycelium-contracts/contracts:/work/high:ro" \
  -v "/tmp/wasm_out:/workspace/out" \
  --entrypoint python3 mycelium-compiler:latest \
  /app/compiler/scripts/compile_batch.py /work/normal /work/high --out /workspace/out
```

`compile_batch.py` prints `OK (<bytes>)` / `FAIL` per contract and a final
`SUMMARY: <ok>/<total>` with the failure list; it exits non-zero if any
contract failed, so it can gate CI. **Reference baseline in-image: 132/300**
(normal 100/100 + high 32/200).

---

## 💻 Running the Compiler Locally (without Docker)

The compiler is pure-Python (stdlib only). It needs a `stellar` binary on
`PATH`; if absent, `ensure_stellar_cli()` auto-downloads the pinned v27.0.0 for
your OS/arch into `codegen/bin/` (Linux x86_64, macOS x86_64/arm64, Windows
x86_64).

```bash
# From the compiler/ directory
python3 -m mycelium_compiler.main path/to/contract.py -o build/target.wasm
```

| Helper script | What it does |
|---------------|--------------|
| `scripts/build_all_contracts.py` | compiles every `*.py` in `contracts/contracts/` one-by-one, prints a pass/fail summary, exits non-zero on any failure |
| `scripts/compile_batch.py` | batch-compiles arbitrary directories (designed for in-image, offline use) |
| `scripts/run_stress_tests.py` | stress harness for the compiler |

Run the unit tests with:

```bash
pytest compiler/tests
```

---

## 🛠️ Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `stellar` exits **127** | missing `libdbus-1-3 / libudev1 / libssl3` | install them (already handled in the Dockerfile) |
| Offline build can't resolve `soroban-sdk` | SDK version mismatch between Dockerfile and `core.py` | pin both to the same version (currently `26.1.0`) |
| `TypeError: ... unsupported type ...` | a state var/arg/return uses a type outside the supported set | use a supported primitive/container (see the mapping table) |
| Value used as `Option<T>` unexpectedly | a `storage.get` result is compared to `None` somewhere | give the read a concrete default `get(key, default)` if you want a plain value |
| `Compiled WASM not found` | cargo build produced no artifact (compile error upstream) | read the `--- Cargo STDERR ---` block in the logs for the real Rust error |

---

## 🔗 Related Docs

- [ide.md](./ide.md) — the Web IDE that wraps this compiler behind a `/compile`
  endpoint and a Docker sandbox, plus GitHub-backed workspaces and on-chain
  deployment.
</content>
</invoke>
