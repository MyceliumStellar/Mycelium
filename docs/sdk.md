# Mycelium SDK — Codebase Guide

The SDK (`sdk/mycelium_sdk/`, published as `mycelium-sdk`) is the **agent
runtime core**. It manages signing keypairs, builds and submits Soroban
transactions, wraps every on-chain contract with typed Python clients, runs
AI-powered agent loops, encrypts wallets at rest, and provides the glue between
off-chain intelligence and on-chain state.

---

## Package layout

```
sdk/mycelium_sdk/
├── __init__.py           # Package exports façade
├── context.py            # AgentContext — transaction builder, simulation, signing
├── contract_client.py    # ContractClient — spec-driven typed proxy
├── crypto.py             # AES-GCM-256 wallet encryption (PBKDF2)
├── hive.py               # HiveClient — on-chain agent directory client
├── spec.py               # Contract spec parser, cache, argument marshaller
├── rpc.py                # Soroban RPC submit + retry + polling wrapper
├── scval.py              # Shortcut SCVal constructors (u64, address, etc.)
├── agent_loop.py         # run_agent_loop — one-call AI agent orchestrator
├── constants.py          # RPC nodes, network passphrases, registry addresses
├── logging.py            # Structured logging framework
├── banner.py             # Solid green MYCELIUM console banner
├── events.py             # On-chain event scanning + streaming
├── models.py             # Live AI model catalogue discovery
├── scaffold.py           # Project scaffolding templates (shared with IDE)
├── indexer_client.py     # Off-chain indexer HTTP client
├── adapters/             # LLM provider adapter modules
│   ├── anthropic.py      # Anthropic Claude tool schemas & dispatch
│   └── gemini.py         # Google Gemini function calling mapper
├── contracts/            # Bundled compiled WASMs
│   └── escrow.wasm       # Pre-compiled escrow contract
├── memory/               # Persistent agent memory subsystem
│   ├── __init__.py       # Exports AgentMemory, MemoryAnchorClient
│   ├── agent_memory.py   # AgentMemory — high-level store + anchor
│   ├── anchor.py         # MemoryAnchorClient — on-chain commitment
│   └── backends.py       # FileMemoryBackend, FirestoreMemoryBackend
└── x402/                 # Machine-to-machine commerce primitives
    └── settlement.py     # EscrowPaymentRouter + legacy aliases
```

---

## 1. AgentContext — [`context.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/context.py)

The sovereign execution context. Maps Python calls to live Stellar/Soroban
transactions (signing + RPC submission).

### Design decision: lazy imports

`stellar_sdk` is imported **inside methods**, not at module import time. This is
critical: the DSL package (`mycelium`) re-exports `AgentContext`, and the
compiler imports `mycelium` while building WASM in environments without
`stellar_sdk`. Merely importing the module must stay free of the heavy
dependency.

### Constructors

| Constructor | Use case | Wallet | Example |
|---|---|---|---|
| `AgentContext(keypair_path, network_type, passphrase)` | Full agent — sign + submit | Encrypted JSON | `ctx = AgentContext("wallet.json", "testnet", "pass")` |
| `AgentContext.read_only(network_type)` | Discovery, views — no wallet | Random throwaway | `ctx = AgentContext.read_only("testnet")` |
| `AgentContext.from_keypair(path, StellarNetwork.TESTNET)` | Back-compat enum style | Encrypted JSON | Legacy API |
| `AgentContext(..., dry_run=True)` | Testing — simulate only | Encrypted JSON | Logs would-be txs, never signs |

The `dry_run` flag is also set by the environment variable `MYCELIUM_DRY_RUN=1`
(used by `mycelium test`). Every simulated call is recorded in both
`ctx.dry_run_log` and the global `DRY_RUN_LOG` list.

### Invocation pipeline — `call_contract()`

```
[Python call: ctx.call_contract(cid, "add", [40])]
     │
     ▼
[_marshal_args] ── spec.marshal_args() → SCVal[] (spec-driven int widths)
     │              falls back to _to_scval() per value if spec unavailable
     ▼
[TransactionBuilder] ── append_invoke_contract_function_op
     │
     ▼
[simulate_transaction] ── Soroban RPC (retried on transient errors)
     │
     ├─► Error? → raise RuntimeError("Simulation failed: ...")
     │
     ├─► read_only=True → return decoded sim result (no fee, no signature)
     │
     ├─► dry_run=True → log record, return TxResult(hash="DRY-RUN", ...)
     │
     ▼
[prepare_transaction] ── assemble footprint + fees (retried)
     │
     ▼
[sign] ── Keypair from decrypted wallet
     │
     ▼
[submit_transaction] ── RPC submit (retried, idempotent on TRY_AGAIN_LATER)
     │
     ▼
[get_transaction] ── poll every 2s, 60s timeout
     │
     ├─► FAILED → raise RuntimeError
     ▼
[decode_tx_result] ── TransactionMeta v3/v4, or fall back to sim result
     │
     ▼
TxResult(hash, status="SUCCESS", return_value)
```

### Type marshalling — `_to_scval()`

Converts Python values to Soroban SCVals:

| Python type | SCVal | Notes |
|---|---|---|
| `bool` | `to_bool()` | Must be checked before `int` (bool is int subclass) |
| DSL typed int (`U64(40)`, `I128(...)`) | Width-correct ctor | `_TYPED_INT_CTORS` maps class names to `to_uint64` etc. |
| `int` | `to_int128()` | Default for plain ints |
| `bytes` | `to_bytes()` | Raw bytes |
| `str` (G.../C...) | `to_address()` | Stellar addresses auto-detected |
| `str` (≤ 32 chars, `[a-zA-Z0-9_]`) | `to_symbol()` | Short strings become Symbols |
| `str` (other) | `to_string()` | Longer strings |
| `list` | `to_vec()` | Recursively marshals elements |

### Pure-Python deployment — `deploy_contract()`

Two on-chain steps, each simulated → prepared → signed → submitted → polled:

1. **Upload WASM** (`append_upload_contract_wasm_op`). SHA-256 hash = WASM id;
   re-uploading is a harmless no-op.
2. **Instantiate** (`append_create_contract_op`). Random 32-byte salt per deploy.
   Returns the new `C…` contract address.

No `stellar-cli` / Rust dependency. Used by both `mycelium deploy` and the IDE's
`/api/deploy`.

### Async support

`acall_contract()` wraps `call_contract` in `asyncio.to_thread`, so an agent
can `await` many contract calls concurrently.

---

## 2. ContractClient — [`contract_client.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/contract_client.py)

Spec-driven typed proxy. Methods are discovered from the contract's on-chain
spec:

```python
client = ctx.contract("CCONTRACTID")

# State-changing (sign + submit):
result = client.increment()

# Read-only (simulate only):
count = client.read.get_count()

# Async state-changing:
result = await client.aio.increment()

# Async read-only:
count = await client.aio.read.get_count()
```

Uses `__getattr__` to proxy method calls. Internally calls
`ctx.call_contract(cid, fn_name, args, read_only=...)`.

---

## 3. HiveClient — [`hive.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/hive.py)

Wraps the [Hive Registry contract](./contracts.md#1-hive-registry):

| Method | Calls | Description |
|---|---|---|
| `register(name, capability_hash, endpoint, model, role, desc)` | `register_agent` | Register on-chain. Auto-publishes to indexer. |
| `resolve(name)` | `resolve_agent` | Returns `{address, capability, endpoint, ...}`. |
| `is_registered(name)` | `is_registered` | Boolean check. |
| `discover_agents(prefer_indexer=True)` | Indexer `/agents` or on-chain event scan | O(1) indexed or O(N) on-chain fallback. |
| `update_reputation(name, score)` | `update_reputation` | Update reputation score. |

### Discovery flow

```
discover_agents(prefer_indexer=True)
    │
    ├─► prefer_indexer? ──► GET /agents (hosted indexer)
    │       │
    │       ├─► success → return indexed agents
    │       └─► failure → fall through to on-chain scan
    │
    └─► on-chain event scan ──► getEvents(HIVEMIND_REGISTRY, "agent_registered")
            │
            └─► resolve each name → return agent list
```

---

## 4. EscrowPaymentRouter — [`x402/settlement.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/x402/settlement.py)

Machine-to-machine commerce. Both halves are real on-chain operations:

| Method | Description |
|---|---|
| `create_locked_escrow(provider, amount_xlm, task_hash, token?, timeout?)` | Deploy escrow + lock funds. Validates amount (> 0, ≤ i128 max). Returns escrow contract id. |
| `release_funds(escrow_id, proof)` | `claim_funds(proof)` — release to provider. |
| `split_release(escrow_id, shares, proof)` | `claim_and_split(proof, recipients, amounts)` — split across N swarm members. Reads locked amount, computes exact stroop amounts (remainder on last). |
| `refund(escrow_id)` | `refund()` — reclaim after deadline. |

### Constants

| Constant | Value | Meaning |
|---|---|---|
| `STROOPS_PER_XLM` | 10,000,000 | 1 XLM = 10⁷ stroops |
| `I128_MAX` | 2¹²⁷ − 1 | Soroban i128 ceiling |
| `DEFAULT_ESCROW_TIMEOUT_SECONDS` | 86,400 | 24 hours |

The bundled `escrow.wasm` lives at `mycelium_sdk/contracts/escrow.wasm`. If
missing, `create_locked_escrow` raises `FileNotFoundError` (never returns a mock
address — "Zero Mocks" policy).

---

## 5. Agent Loop — [`agent_loop.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/agent_loop.py) & [`adapters/`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/adapters)

One-call agent orchestrator. Translates `ContractTool` definitions into LLM
tool schemas and runs the conversation loop:

### Anthropic adapter ([`adapters/anthropic.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/adapters/anthropic.py))

1. Builds JSON schemas matching Anthropic's tool call contract.
2. Loop: send messages → check `stop_reason == "tool_use"` → parse args →
   `call_contract()` → append `tool_result` → repeat.

### Gemini adapter ([`adapters/gemini.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/adapters/gemini.py))

1. Converts `ContractTool` defs into Python function instances with docstrings.
2. Passes them to `GenerativeModel` tool array.
3. `enable_automatic_function_calling` handles dispatch automatically.

---

## 6. Wallet encryption — [`crypto.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/crypto.py)

Enforces the "Encrypted Keys" constraint. AES-GCM ciphertext + salt + nonce
serialized as JSON:

| Parameter | Value |
|---|---|
| **Key derivation** | PBKDF2-HMAC-SHA256, **600,000 iterations**, 16-byte salt |
| **Cipher** | AES-GCM with 12-byte random nonce |
| **Payload** | Stellar secret seed (`S…` string) |

`resolve_passphrase()` checks `MYCELIUM_DECRYPT_KEY` env var first, then prompts
interactively. The encrypted wallet JSON format:

```json
{
  "public_key": "G...",
  "encrypted_secret": "<hex>",
  "nonce": "<hex>",
  "salt": "<hex>"
}
```

---

## 7. Persistent agent memory — [`memory/`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/memory)

Full coverage in [memory.md](./memory.md). Quick overview:

- **`AgentMemory`** — high-level API: `remember(key, value)`, `recall(key)`,
  `anchor()`, `verify()`, `rehydrate()`.
- **`MemoryAnchorClient`** — wraps the MemoryAnchor contract: `set_anchor()`,
  `get_anchor()`, `get_version()`, `is_anchored()`.
- **Backends**: `FileMemoryBackend` (JSON on disk), `FirestoreMemoryBackend`
  (Google Cloud).

---

## 8. Off-chain indexer client — [`indexer_client.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/indexer_client.py)

HTTP client for the hosted indexer API:

| Method | Endpoint | Returns |
|---|---|---|
| `get_agents()` | `GET /agents` | All registered agents |
| `get_jobs(status?)` | `GET /jobs?status=...` | Job listings |
| `get_memory(owner)` | `GET /memory/{owner}` | Memory anchors |
| `get_stats()` | `GET /stats` | Network statistics |

Full coverage in [indexer.md](./indexer.md).

---

## 9. Events — [`events.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/events.py)

On-chain event scanning and streaming:

| Function | Description |
|---|---|
| `get_contract_events(rpc, contract_id, start_ledger?)` | One-shot event scan. |
| `stream_events(rpc, contract_id, callback, start_ledger?)` | Continuous polling with callback per event. |
| `decode_event(event)` | Decode raw XDR event to Python dict. |

Used by `mycelium events` CLI and the indexer's ingest worker.

---

## 10. Model discovery — [`models.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/models.py)

Live model catalogue discovery. Used by `mycelium init` (model selection) and
the IDE's `POST /api/models`:

| Function | Description |
|---|---|
| `supports_discovery(framework)` | Whether the framework has a model list API. |
| `requires_api_key(framework)` | Whether an API key is needed (vs. base URL for local). |
| `list_models(framework, api_key?, base_url?)` | Query the provider and return model names. |

Supports: Gemini, Anthropic, OpenAI, Ollama.

---

## 11. RPC helpers — [`rpc.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/rpc.py)

Transient retry wrapper:

| Function | Description |
|---|---|
| `with_retry(fn, label, max_retries=3)` | Retry on `RequestException` / `ConnectionError`. |
| `submit_transaction(rpc, tx)` | Submit with retry; re-sends same signed tx on `TRY_AGAIN_LATER` (idempotent). |

---

## 12. SCVal helpers — [`scval.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/scval.py)

Shortcut constructors for building Soroban SCVals without importing
`stellar_sdk.scval`:

```python
from mycelium_sdk.scval import u64, u32, address, symbol, to_bytes

val = u64(42)
addr = address("GABCDEF...")
```

---

## 13. Constants — [`constants.py`](file:///home/ansh/Mycelium/sdk/mycelium_sdk/constants.py)

Network-aware constants:

| Constant | Value |
|---|---|
| `SOROBAN_RPC_URLS` | `{testnet: "https://soroban-testnet.stellar.org", mainnet: "https://mainnet.sorobanrpc.com"}` |
| `HORIZON_URLS` | `{testnet: "https://horizon-testnet.stellar.org", mainnet: "https://horizon.stellar.org"}` |
| `HIVEMIND_REGISTRY_ADDRESS` | `CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC` |
| `MEMORY_ANCHOR_ADDRESS` | `CAC27VKJEPDJJNI36NP7D7VH6WCHT6N5EITKSKPZIQNWA2VPEPBIXJSB` |
| `native_token_address(network)` | Returns the SAC address for native XLM |

---

## Related docs

- [`contracts.md`](./contracts.md) — the on-chain contracts the SDK wraps.
- [`cli.md`](./cli.md) — the CLI that drives the SDK from the terminal.
- [`indexer.md`](./indexer.md) — the off-chain indexer the SDK queries.
- [`memory.md`](./memory.md) — persistent agent memory subsystem.
- [`compiler.md`](./compiler.md) — the compiler (shares the DSL with the SDK).
