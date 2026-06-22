# Mycelium Python-to-Soroban Compiler

The Mycelium Compiler (`mycelium-compiler`) is a high-performance Python AST parser and transpilation engine that converts Python-DSL smart contracts into highly optimized, secure WebAssembly (WASM) binaries for the Stellar/Soroban virtual machine.

---

## 🏗️ Compiler Architecture

The compilation process is structured into four main phases:

```
┌──────────────┐      ┌────────────────┐      ┌─────────────────┐      ┌─────────────┐
│  Python DSL  │ ───> │  AST Parsing   │ ───> │ Type Validation │ ───> │ Transpilation│
│  (Source)    │      │  (parser.py)   │      │ (validator.py)  │      │ (codegen/)  │
└──────────────┘      └────────────────┘      └─────────────────┘      └──────┬──────┘
                                                                              │
                                                                              ▼
┌──────────────┐      ┌────────────────┐      ┌─────────────────┐      ┌─────────────┐
│ Soroban WASM │ <─── │   Rust Cargo   │ <─── │   Stellar CLI   │ <─── │  Rust Code  │
│   (Binary)   │      │     Build      │      │   Compilation   │      │ (src/lib.rs)│
└──────────────┘      └────────────────┘      └─────────────────┘      └─────────────┘
```

### 1. AST Parsing (`parser.py`)
- Reads the Python source file and converts it into a Python Abstract Syntax Tree (AST) using Python's native `ast` library.
- Extracts module-level constants, `@contract` definitions, storage variables, and contract function schemas.
- Parses auxiliary classes representing custom structs, events, interfaces, and constant-based enums.

### 2. Type & AST Validation (`validator.py`)
- Ensures that all types specified in the Python contract strictly conform to Soroban-compatible primitives and collection types.
- Asserts that all function signatures, return types, and storage variables are valid.
- Supported Primitives: `int`, `str`, `bytes`, `bool`, `Symbol`, `i32`, `i64`, `i128`, `u32`, `u64`, `Address`, `U256`, `U128`, `U64`, `U32`, `I128`, `I32`, `Bool`, `Env`.
- Supported Collections: `Map[K, V]`, `Vec[T]`, `Bytes[N]`, `DynArray[T, N]`, `list`, `tuple`.

### 3. Rust Code Generation (`codegen/`)
- **Storage Type Inference (`inferrer.py`)**: Traverses function logic to infer the types of local variables and on-chain storage states to construct statically typed Rust equivalents.
- **Transpiler (`transpiler.py` & `core.py`)**: Translates Python statements, loops, branches, assignments, and expressions into clean, memory-safe, idiomatic Soroban Rust code.
- **Features**:
  - Automatically handles local variable pre-declaration.
  - Implements storage read/write virtualization (mapping `self.balances[key]` to Soroban persistent/instance storage access).
  - Injects contextual state wrappers (e.g., `msg_sender.require_auth()`, block sequences, and transaction timestamps).

### 4. Compilation & Bootstrapping (`core.py`)
- Emits temporary Cargo workspaces with optimal release settings:
  - `opt-level = "z"` (optimized for minimal WASM size).
  - `overflow-checks = true`.
  - Link-Time Optimization (`lto = true`) and single-unit compilation.
- **Stellar CLI Bootstrapper**: Checks for `stellar` in the system path. If not found, it automatically downloads the certified `stellar-cli` executable for your specific platform/architecture.
- Compiles the Rust intermediate file into a `.wasm` file.

---

## 🚀 Installation & CLI Usage

Install the compiler package:
```bash
pip install mycelium-compiler
```

Compile a Python contract source file to WASM:
```bash
mycelium compile my_contract.py -o build/my_contract.wasm
```

### Script Execution (Python API)
You can also compile contracts programmatically inside Python scripts:

```python
from mycelium_compiler.main import compile_file

compile_file("my_contract.py", "build/my_contract.wasm")
```

---

## 📝 DSL Contract Example

The compiler translates Python files looking like this:

```python
from mycelium import contract, external, view, U64, Address, Map

@contract
class TokenCounter:
    # On-chain state variables
    balances: Map[Address, U64]
    owner: Address

    @external
    def initialize(self, owner: Address):
        self.owner = owner

    @external
    def mint(self, to: Address, amount: U64):
        # Implicitly requires auth from the owner (under the hood)
        if msg_sender != self.owner:
            panic("Unauthorized")
        
        current = self.balances.get(to, 0)
        self.balances[to] = current + amount

    @view
    def get_balance(self, account: Address) -> U64:
        return self.balances.get(account, 0)
```

---

## 🛠️ Sandbox Fallback Execution

When running inside cloud sandboxes or serverless backend instances (e.g., Render or AWS Lambda) where Docker is restricted, the compiler features a native python runner fallback that executes the cargo compiler in-process, bypassing the container requirement while enforcing standard resource safety limits.
