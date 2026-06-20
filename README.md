# 🌐 Mycelium

### The Python-First Framework for Smart Contract Development and Agentic Orchestration on Stellar

[![Stellar Network](https://img.shields.io/badge/Powered%20by-Stellar%20Soroban-000000?style=flat&logo=stellar&logoColor=white)](https://stellar.org)
[![Python Version](https://img.shields.io/badge/Python-3.10%20%7C%203.11-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-blue)](https://opensource.org/licenses/MIT)

Mycelium is a developer platform designed to eliminate the "Rust tax" for smart contract development on the Stellar network. It provides a Python-first compiler, SDK, and command-line utility suite that enables autonomous, on-chain agents to write logic, compile directly to WebAssembly, deploy to Soroban ledgers, and execute peer-to-peer economic coordination natively.

---

## ⚡ Core Philosophy

Writing smart contracts shouldn't require learning complex domain-specific languages or low-level systems programming. Mycelium allows developers to leverage Python's clean, strictly-typed syntax to deploy production-ready Soroban contracts. It acts as the **operating system for autonomous economies**, allowing agents to discover, coordinate, and transact natively on the blockchain.

```python
# A simple, strictly-typed Mycelium Agent Contract
from mycelium import contract, Address, Env, u32

@contract
class EscrowAgent:
    @contract.init
    def __init__(self, admin: Address):
        self.admin = admin
        self.balance = 0

    def deposit(self, env: Env, amount: u32) -> u32:
        self.balance += amount
        env.events.publish("deposit", amount)
        return self.balance
```

---

## 📁 Repository Map

```
Mycelium/
├── requirements.txt                    # Root dependency definitions
├── compiler/                           # Component 1: Python-to-Soroban Compiler
│   ├── mycelium_compiler/              # AST parsers, type validators, and Rust codegen
│   ├── scripts/
│   │   ├── build_all_contracts.py      # Build suite for core contracts
│   │   └── run_stress_tests.py         # Stress test execution suite
│   └── tests/                          # Compiler unit test suite
├── sdk/                                # Component 2: Mycelium SDK (Python Agent library)
│   ├── mycelium_sdk/                   # AgentContext, ledger clients, and X402 billing
│   └── tests/
├── cli/                                # Component 3: CLI Utility Suite (mycelium-cli)
│   ├── mycelium_cli/                   # Scaffold, verify, compile, and deploy commands
│   └── tests/
├── contracts/                          # Component 4: Standard Contract Libraries
│   ├── contracts/                      # Core suite of 100 benchmark contracts
│   ├── stress_tests/                   # Stress-test suite of 150 contracts
│   ├── escrow.py                       # Ephemeral Escrow / X402 settlement contract
│   └── hive_registry.py                # Agent capability lookup registry
├── docs/                               # Developer Guides
│   └── ide.md                          # API specifications and IDE architecture
└── ide/                                # Component 5: Web IDE Playground
    ├── frontend/                       # Next.js web application with Monaco Editor
    └── backend/                        # FastAPI compiling sandbox
```

---

## ⚙️ Compilation Benchmarks

The Mycelium compiler uses a high-performance AST transpiler to generate highly-optimized Soroban Rust SDK structures before serializing to WebAssembly.

| Metric | Benchmark Result | Technical Detail |
| :--- | :--- | :--- |
| **Transpilation Speed** | `< 5 ms` | Python AST parser to clean Rust source |
| **Cargo Compile Speed** | `8.5s - 10s` | Optimized release targets (warm cache) |
| **WASM Binary Footprint** | `1.1 KB - 3.8 KB` | Optimized for low-cost ledger footprint |
| **Compiler Code Coverage** | `100% (250 / 250)` | Full build success across core & stress contracts |

### Compiled Artifact Size Benchmarks (Samples)
* `01_simple_storage.wasm` — **1.1 KB**
* `02_erc20_token.wasm` — **3.8 KB**
* `03_ownable.wasm` — **1.9 KB**
* `05_multisig.wasm` — **3.2 KB**
* `15_dao.wasm` — **3.5 KB**

---

## 🚀 Getting Started

### 1. Configure the Python CLI
Set up your virtual environment and install the package definitions:
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Scaffold a New Agent
Initialize your directory structure and test locally:
```bash
# Verify CLI syntax
python -m cli.mycelium_cli.main --help

# Initialize project template
python -m cli.mycelium_cli.main init my_agent
```

### 3. Start the Compiler Sandbox (Backend)
The Web IDE communicates with a local compiler sandbox running FastAPI:
```bash
cd ide/backend
uvicorn main:app --reload --port 8000
```

### 4. Run the Web IDE (Frontend)
Boot up the Next.js visual playground:
```bash
cd ide/frontend
npm run dev
```
Open `http://localhost:3000` to launch the visual compiler playground.

---

## 🧪 Testing Suite

Run unit and integration tests across the AST compiler using `pytest`:
```bash
PYTHONPATH=compiler pytest compiler/tests/
```

---

## 📄 License

Mycelium is open-source software licensed under the [MIT License](LICENSE).
