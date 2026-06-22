# 🌐 Mycelium

### The Python-First Framework for Smart Contract Development and Agentic Orchestration on Stellar

[![Stellar Network](https://img.shields.io/badge/Powered%20by-Stellar%20Soroban-000000?style=flat&logo=stellar&logoColor=white)](https://stellar.org)
[![Python Version](https://img.shields.io/badge/Python-3.10%20%7C%203.11-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-blue)](https://opensource.org/licenses/MIT)

Mycelium is a comprehensive developer platform designed to eliminate the "Rust tax" for smart contract development on the Stellar network. It provides a Python-first compiler, SDK, CLI, and Web IDE that enables autonomous, on-chain agents to author contract logic, compile directly to WebAssembly, deploy to Soroban ledgers, and execute peer-to-peer economic coordination natively.

---

## ⚡ Core Philosophy & Architecture

Writing smart contracts shouldn't require learning low-level systems languages. Mycelium allows developers to leverage Python's clean, strictly-typed syntax to deploy production-ready Soroban contracts. It acts as the **operating system for autonomous economies**, allowing agents to discover, coordinate, and transact natively on the blockchain.

```
                  ┌──────────────────────────────────────────┐
                  │            Developer Workflow            │
                  │   - mycelium CLI (init, compile, deploy) │
                  │   - Web IDE Playground (FastAPI + Next.js)│
                  └────────────────────┬─────────────────────┘
                                       │
                                       ▼
                  ┌──────────────────────────────────────────┐
                  │        Mycelium Compiler Pipeline        │
                  │  - Python AST parsing & static validation│
                  │  - Transpilation to optimized Soroban Rust│
                  │  - Pinned stellar-cli 27.0.0 WASM build  │
                  └────────────────────┬─────────────────────┘
                                       │
                                       ▼
                  ┌──────────────────────────────────────────┐
                  │          Stellar/Soroban Ledger          │
                  │   - Hive Registry (Discovery Contract)   │
                  │   - Escrow Contracts (x402 Micropayments)│
                  └──────────────────────────────────────────┘
```

---

## 📁 Repository Map

The repository is structured to separate individual components into clean Python packages and visual layers:

```
Mycelium/
├── requirements.txt           # Root developer requirements
├── pytest.ini                 # Unified test configurations for all components
├── mycelium/                  # Facade & DSL Package (distribution: mycelium-stellar)
│   ├── types.py               # AST decorator validations, Env mocks, and type wrappers
│   └── pyproject.toml         # Meta-package linking SDK, CLI, and Compiler dependencies
├── compiler/                  # Component 1: Python-to-Soroban Compiler
│   ├── mycelium_compiler/     # AST parsers, type validators, and Rust codegen
│   └── tests/                 # Compiler unit tests & benchmark suite
├── sdk/                       # Component 2: Mycelium SDK (Agent Runtime Library)
│   ├── mycelium_sdk/          # AgentContext, HiveClient, x402 settlement, crypto engines
│   └── tests/                 # SDK test suite (including live testnet specs)
├── cli/                       # Component 3: Command Line Suite (mycelium-cli)
│   ├── mycelium_cli/          # Command controllers (init, compile, deploy, resolve, status, etc.)
│   └── tests/                 # CLI execution tests
├── ide/                       # Component 4: Web IDE Playground
│   ├── frontend/              # Next.js UI using Monaco Editor & reactive visualizations
│   └── backend/               # FastAPI compiler sandbox running isolated Docker workers
├── docs/                      # Developer Internal Guides (Reference Manuals)
│   ├── compiler.md            # Detailed compiler AST parsing & transpiler internals
│   ├── ide.md                 # Sandbox execution configurations & API specification
│   ├── dsl.md                 # Mycelium DSL type mapping rules and decorators
│   ├── sdk.md                 # SDK core classes, lifecycle, and adapter designs
│   ├── cli.md                 # CLI structures, configurations, and commands dispatch
│   └── contracts.md           # Hive Registry, Escrow Contract, and A2A coordination demo
├── sdk.md                     # User-Facing SDK Guide (Stellar/Soroban Integration Guide)
├── cli.md                     # User-Facing CLI Reference (Terminal Interface Manual)
└── ROADMAP.md                 # Live development roadmap, features, and scale plans
```

---

## 🚀 Getting Started

### 1. Installation

A single meta-package installs the entire toolchain — the `mycelium` DSL, the `mycelium_sdk` library, the `mycelium` CLI wrapper, and the Python→WASM compiler:

```bash
pip install mycelium-stellar
mycelium --help
```

For developer workflows using editable paths (cloned codebase):
```bash
python -m venv venv
source venv/bin/activate
pip install -e ./compiler -e ./sdk -e ./cli -e ./mycelium
mycelium --help
```

### 2. Scaffold a Project

Use the interactive wizard to generate the default configuration and templates:
```bash
mycelium init my_agent
cd my_agent
```

This creates a standard project structure:
- `mycelium.toml`: Configuration file.
- `contract.py`: Your smart contract authored in Python DSL.
- `agent.py`: Your agent script leveraging the SDK context.
- `.mycelium/`: Ignored folder holding the agent's keypair.

### 3. Generate secure wallet keys

```bash
mycelium newwallet
```
This generates a secure Ed25519 wallet, deriving an AES-256-GCM encryption key from your passphrase. The secret seed is encrypted at rest inside `.mycelium/wallet.json`.

### 4. Compile the smart contract

```bash
mycelium compile
```
This runs the Python source code through static AST checks and compiles it into an optimized WebAssembly contract binary at `build/contract.wasm`.

### 5. Deploy to Stellar Testnet

```bash
mycelium deploy --network testnet
```
This automatically funds the agent wallet using Stellar Friendbot (if balance is 0), deploys the contract to Stellar testnet via an isolated sandbox worker, and writes the `contract_id` back to `mycelium.toml`.

### 6. Register Agent capabilities on-chain

```bash
mycelium register
```
This submits a signed transaction to the global Hive Registry, mapping the agent's unique name to its public address, service endpoint, and capability tags list.

---

## 🧪 Compilation Benchmarks

The Mycelium compiler compiles Python AST elements into isomorphic Soroban Rust structures, producing compact and low-gas WebAssembly binaries:

| Metric | Benchmark Result | Technical Detail |
| :--- | :--- | :--- |
| **AST Transpilation Speed** | `< 5 ms` | Python AST node lowering to Rust representation |
| **Cargo Build Time** | `8.5s - 10s` | Optimized WASM release compilation (warm cache) |
| **WASM Binary Footprint** | `1.1 KB - 3.8 KB` | Leverages release profiles, LTO, and panic abort |
| **Standard Contracts Coverage** | `100% (100 / 100)` | Full compilation validation across baseline contracts |

---

## 🏃 Running the IDE Playground

The Web IDE provides a local developer sandbox:
1. Boot the environment using the startup runner:
   ```bash
   ./start.sh
   ```
2. Open your browser and navigate to `http://localhost:3000/playground` to access the editor, compile, deploy, and inspect the reactive network visualizations.

---

## 🧪 Testing the codebase

Run the offline unit and integration test suites:
```bash
pytest
```
To run the live testnet transactions suite (which funds wallets via Friendbot and performs real on-chain interactions):
```bash
MYCELIUM_LIVE_TESTS=1 pytest sdk/tests/test_live_testnet.py
```

---

## 📄 Documentation Reference Hub

We maintain comprehensive documentation for all levels of developers:

### User Manuals (Root)
- 🎒 **[SDK User Guide](file:///home/ansh/Mycelium/sdk.md)**: Details class methods, transaction simulations, event subscriptions, and AI adapter wiring.
- 🛠️ **[CLI Command Reference](file:///home/ansh/Mycelium/cli.md)**: Explains every command, interactive wizard, configuration flags, and wallet encryption.

### Codebase Internal Guides (`docs/` folder)
- 🔠 **[DSL Internals Guide](file:///home/ansh/Mycelium/docs/dsl.md)**: Explains the decorators, simulated Env methods, and type conversions.
- ⚙️ **[Compiler Codebase Guide](file:///home/ansh/Mycelium/docs/compiler.md)**: Details the parser visitor, validator checks, type inferer, and transpiler rules.
- 🧠 **[SDK Codebase Guide](file:///home/ansh/Mycelium/docs/sdk.md)**: Inspects the context initialization, cryptography, spec parsing, and event loops.
- 🔧 **[CLI Codebase Guide](file:///home/ansh/Mycelium/docs/cli.md)**: Details command structures, config loader, and terminal rendering styles.
- 🔌 **[IDE Architecture Guide](file:///home/ansh/Mycelium/docs/ide.md)**: Focuses on backend endpoints, database structure, and the Docker compile sandbox.
- 📜 **[Contracts and Demos](file:///home/ansh/Mycelium/docs/contracts.md)**: Details the on-chain Hive Registry, Escrow contracts, and Multi-Agent A2A coordinating logic.
