# Mycelium

Mycelium is a Python-first framework designed to eliminate the "Rust tax" for smart contract development and agentic orchestration on the Stellar network. It bridges the multi-million developer Python AI ecosystem with Soroban, enabling autonomous, on-chain agents to write logic, compile, deploy, discover, and settle economic transactions natively.

---

## 📁 Project Structure

```
Mycelium/
├── requirements.txt                    # Top-level dependencies for local run & backend
├── compiler/                           # Component 1: Python-to-Soroban Compiler
│   ├── mycelium_compiler/              # AST parsing, type validator, codegen
│   ├── scripts/
│   │   ├── build_all_contracts.py      # Build suite for core contracts
│   │   └── run_stress_tests.py         # Stress test execution suite
│   └── tests/                          # Compiler unit test suite
├── sdk/                                # Component 2: Mycelium SDK (Python Agent Runtime library)
│   ├── mycelium_sdk/                   # AgentContext, client, x402 settlement
│   └── tests/
├── cli/                                # Component 2: Mycelium CLI Tool (mycelium-cli)
│   ├── mycelium_cli/                   # init, check, compile, deploy, agent commands
│   └── tests/
├── contracts/                          # Component 3 & 4: Core Smart Contracts
│   ├── contracts/                      # Core suite of 100 contracts
│   ├── stress_tests/                   # Stress test suite of 150 contracts
│   ├── escrow.py                       # Ephemeral Escrow / x402 contract
│   └── hive_registry.py                # Agent capability registry
├── docs/                               # Documentation
│   └── ide.md                          # Detailed API and structure docs for Web IDE
└── ide/                                # Component 5: Mycelium Web IDE
    ├── frontend/                       # Web UI (Next.js with TypeScript, Monaco Editor)
    └── backend/                        # API Gateway & Sandbox Manager (FastAPI)
```

---

## ⚡ Compiler Benchmarks & Metrics

The Mycelium compiler uses a custom AST validator and transpiler (`RustTranspiler`) to translate Python contracts into highly-optimized Soroban Rust SDK codes.

### Benchmark Statistics

| Metric | Value | Details |
| :--- | :--- | :--- |
| **Transpilation Speed** | **< 5 ms** | Python AST parser and validator to Rust source conversion |
| **Cargo Compile Speed** | **8.5s - 10s** | Cargo build with optimized release targets (warm cache) |
| **WASM Binary Size** | **1.1 KB - 3.8 KB** | Highly optimized for Soroban ledger cost efficiency |
| **Average WASM Size** | **2.4 KB** | Minimized byte footprints through `opt-level = "z"` and LTO |
| **Core Suite Success** | **100% (100 / 100)** | Fully builds `contracts/contracts/` contract library |
| **Stress Suite Success** | **100% (150 / 150)** | Fully builds `contracts/stress_tests/` validator suite |

### Compiled Artifact Footprints (Sample size)

* `01_simple_storage.wasm` — **1.1 KB**
* `02_erc20_token.wasm` — **3.8 KB**
* `03_ownable.wasm` — **1.9 KB**
* `04_eth_vault.wasm` — **1.8 KB**
* `05_multisig.wasm` — **3.2 KB**
* `06_timelock.wasm` — **2.7 KB**
* `07_access_control.wasm` — **2.1 KB**
* `15_dao.wasm` — **3.5 KB**
* `20_oracle.wasm` — **2.0 KB**

---

## ⚙️ Installation & Setup

### 1. Python Environment Setup
Create a virtual environment and install dependencies:
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Running Compiler Diagnostics & CLI
```bash
# Verify compiler CLI help command
python -m cli.mycelium_cli.main --help

# Run all 100 core contracts compilation suite
PYTHONPATH="compiler:." venv/bin/python3 compiler/scripts/build_all_contracts.py

# Run all 150 stress test contracts
PYTHONPATH="compiler:." venv/bin/python3 compiler/scripts/run_stress_tests.py
```

### 3. Running the Web IDE Backend
Configure the backend connection details in `ide/backend/.env` and start FastAPI:
```bash
cd ide/backend
uvicorn main:app --reload --port 8000
```

### 4. Running the Web IDE Frontend
Install npm dependencies and start Next.js dev server:
```bash
cd ide/frontend
npm install
npm run dev
```

---

## 🧪 Testing

Run test suites using `pytest`:
```bash
PYTHONPATH=compiler pytest compiler/tests/
```
