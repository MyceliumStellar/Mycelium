# Mycelium

Mycelium is a Python-first framework designed to eliminate the "Rust tax" for smart contract development and agentic orchestration on the Stellar network. It bridges the multi-million developer Python AI ecosystem with Soroban, enabling autonomous, on-chain agents to write logic, compile, deploy, discover, and settle economic transactions natively.

## Project Structure

```
Mycelium/
├── requirements.txt                    # Top-level dependencies for local run & backend
├── compiler/                           # Component 1: Python-to-Soroban Compiler
│   ├── mycelium_compiler/              # AST parsing, type validator, codegen
│   └── tests/
├── sdk/                                # Component 2: Mycelium SDK (Python Agent Runtime library)
│   ├── mycelium_sdk/                   # AgentContext, client, x402 settlement
│   └── tests/
├── cli/                                # Component 2: Mycelium CLI Tool (mycelium-cli)
│   ├── mycelium_cli/                   # init, check, compile, deploy, agent commands
│   └── tests/
├── contracts/                          # Component 3 & 4: Core Smart Contracts (written in mycelium python)
│   ├── hive_registry.py                # Agent capability registry
│   └── escrow.py                       # Ephemeral Escrow / x402 contract
└── ide/                                # Component 5: Mycelium Web IDE
    ├── frontend/                       # Web UI (Next.js with TypeScript, Monaco Editor)
    └── backend/                        # API Gateway & Sandbox Manager (FastAPI)
```

## Installation & Setup

1. **Python Environment Setup**:
   Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Run the Compiler & CLI**:
   The compiler and CLI tools can be executed directly:
   ```bash
   python -m cli.mycelium_cli.main --help
   ```

3. **Running the Web IDE Backend**:
   Navigate to the backend folder and start FastAPI:
   ```bash
   cd ide/backend
   uvicorn main:app --reload --port 8000
   ```

4. **Running the Web IDE Frontend**:
   Navigate to the frontend folder, install dependencies and start the Next.js dev server:
   ```bash
   cd ide/frontend
   npm install
   npm run dev
   ```

## Development and Testing

Run test suites using `pytest`:
```bash
PYTHONPATH=compiler pytest compiler/tests/
```
