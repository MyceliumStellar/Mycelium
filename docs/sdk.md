# Mycelium SDK Internal Architecture Guide

This document is the internal code guide for developers modifying or extending the Mycelium SDK (`sdk/mycelium_sdk/` package). It details the module interfaces, transaction execution pipelines, cryptographic functions, and framework adapter layers.

---

## 🗂️ Package Architecture

```
sdk/mycelium_sdk/
├── __init__.py           # Package exports façade
├── context.py            # AgentContext (Transaction builder, simulation, signing)
├── crypto.py             # PBKDF2 + AES-GCM-256 wallet cryptography
├── hive.py               # HiveClient (On-chain agent directory resolver)
├── spec.py               # Spec parser, cache, and argument marshaller
├── contract_client.py    # Spec-driven ContractClient class proxy namespace
├── agent_loop.py         # run_agent_loop (Orchestrator runner)
├── constants.py          # RPC nodes, network passphrases, and registry addresses
├── logging.py            # Structured logging framework
├── banner.py             # Solid green MYCELIUM console log banner
├── rpc.py                # Soroban RPC submit/get-transaction retry wrapper
├── scval.py              # Shortcut SCVal constructors (u64, address, etc.)
├── adapters/             # LLM provider adapter modules
│   ├── anthropic.py      # Anthropic tool schemas & dispatch wrapper
│   └── gemini.py         # Google Gemini function calling mapper
└── x402/
    └── settlement.py     # EscrowPaymentRouter + legacy billing manager
```

---

## ⚙️ Module Deep-Dives

### 1. `context.py` (Lazy-Loading & Execution)
`stellar_sdk` is imported **lazily inside methods** rather than at module import time. This design choice is critical: the smart contract package (`mycelium`) re-exports `AgentContext`, and the compiler imports `mycelium` during WASM compilation within environments that do not have `stellar_sdk` installed. Importing this module must remain free of the heavy on-chain dependency.

#### Invocation Pipeline (`call_contract`):
```
[Invoke Call] 
     │
     ▼
[fetch_spec] ──────► [marshal_args] ──► [TransactionBuilder]
                                                │
                                                ▼
                                      [simulate_transaction] (Soroban RPC)
                                                │
                                                ├─► (Error?) ──► [Raise simulation fail]
                                                ▼
                                      [prepare_transaction] (Estimated fees/footprint)
                                                │
                                                ▼
                                           [Sign tx] (Keypair decryption)
                                                │
                                                ▼
                                       [submit_transaction] (RPC Loop)
                                                │
                                                ▼
                                       [get_transaction] (Poll till settled)
                                                │
                                                ▼
                                       [decode_tx_result] (Return value)
```

- **Simulation Fallback**: Simulation returns the function's return value. We use it as the authoritative return value for state-changing calls because the post-settle transaction metadata `TransactionMetaV4` under Protocol 23 is not decodable by older `stellar-sdk` libraries.

---

### 2. `crypto.py` (Wallet Encryption at Rest)
Enforces the "Encrypted Keys" constraint. AES-GCM ciphertext along with salt and nonce values are serialized as JSON:
- Key derivation: `PBKDF2HMAC` using SHA-256 with **600,000 iterations** to derive a 256-bit key.
- Encryption cipher: `AESGCM` with a 12-byte random initialization vector.
- Secret payload: Stellar secret seed string (starting with `S...`).
- Salt length: 16 bytes.

---

### 3. `spec.py` & `contract_client.py` (Dynamic Interface Mapping)
Provides type-safe client methods dynamically:
- `fetch_spec(soroban_rpc, contract_id)`: Fetches the `SCSpecFunctionV0` spec from the ledger state and caches it per `(rpc_url, contract_id)` to prevent redundant RPC traffic.
- `ContractClient` uses standard Python `__getattr__` overriding to proxy methods. Calling `client.increment()` translates into `call_contract(..., "increment")`.
- It splits the namespace:
  - `client.<fn>()`: State-changing sync transactions.
  - `client.read.<fn>()`: Read-only simulated transactions.
  - `client.aio.<fn>()`: Async state-changing transactions (run on worker threads via `asyncio.to_thread`).
  - `client.aio.read.<fn>()`: Async read-only simulated transactions.

---

### 4. `agent_loop.py` & `adapters/` (LLM Tool Execution)
Enables one-call agent logic. It translates `ContractTool` definitions into LLM tool schemas:
- **Anthropic Adapter**: Builds JSON schemas matching Anthropic tool call contracts. The loop intercepts the model's `stop_reason == "tool_use"`, parses the arguments, dispatches to the corresponding `call_contract` execution client, and appends `tool_result` blocks back to the conversation thread.
- **Gemini Adapter**: Converts `ContractTool` definitions into Python function instances decorated with docstrings. It passes them to the `GenerativeModel` tool array. The Gemini API's `enable_automatic_function_calling` resolves, calls, and updates state automatically.

---

### 5. `x402/settlement.py` (Micropayment Routers)
Coordinates agent transactions:
- `EscrowPaymentRouter`: Integrates the compiled `escrow.wasm` contract.
- `create_locked_escrow`: Instantiates a new escrow contract, funding it with target tokens. Raises a `NotImplementedError` if the compiled `escrow.wasm` is not found, ensuring mock addresses are never returned (satisfying "Zero Mocks").
- `release_funds`: Submits a claim transaction passing the task completion verification proof bytes.
