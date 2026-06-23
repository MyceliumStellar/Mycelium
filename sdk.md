# Mycelium SDK Developer Guide

This document is the definitive developer guide for the Mycelium SDK (`mycelium-stellar` Python package). The SDK enables off-chain agents, autonomous tasks, and AI frameworks to securely interact with on-chain Soroban smart contracts, settle payments via the x402 protocol, and register capabilities within the Hive Registry.

---

## 🧭 Core Architectural Concept

The SDK acts as the execution client for autonomous agents. It bridges the gap between dynamic, AI-driven programming models (such as Gemini, Anthropic, or LangGraph) and the strictly-typed, transaction-metered world of Stellar's Soroban virtual machine.

All SDK actions share a foundational constraint system:
- **Zero Mocks**: All read/write calls route directly through live Horizon/Soroban RPC nodes. Mocks are avoided in the on-chain paths.
- **Encrypted Keys**: Private keys are encrypted at rest via AES-256-GCM + PBKDF2. Decryption occurs only in-memory during contract sign-and-submit execution phases.
- **Implicit Constants**: Standard contract addresses (like the Hive Registry constant) are embedded within the library, eliminating boilerplate.

---

## 🧠 Core SDK Classes

### 1. `AgentContext`
The central manager coordinating wallet decryption, Horizon node RPC client connections, fee estimations, and on-chain signing.

#### Initialization
```python
from mycelium import AgentContext

# Initializing a context (implicitly decrypts wallet.json)
ctx = AgentContext(
    keypair_path=".mycelium/wallet.json",
    network_type="testnet",       # "testnet", "mainnet", or "local"
    passphrase="your-passphrase"   # Will check MYCELIUM_DECRYPT_KEY env if omitted
)
```

#### Read-Only Context (Wallet-free)
For read-only views (e.g., querying another agent's reputation or public address), a wallet is not required. A read-only context can be initialized without decrypting or prompting for keys:
```python
ctx = AgentContext.read_only(network_type="testnet")
```

#### Invoking Contracts (`call_contract`)
The `call_contract` method manages the complete transaction lifecycle:
1. **Argument Marshalling**: Converts Python types to Soroban `SCVal` types (cached from on-chain spec).
2. **Simulation**: Simulates the transaction on-ledger first to verify success and estimate gas/footprints.
3. **Preparation**: Appends footprints and fees automatically.
4. **Signing**: Cryptographically signs the transaction envelope.
5. **Submission**: Transmits to Soroban RPC (retried on transient errors).
6. **Polling**: Polls the ledger status until transaction settlement is confirmed.

```python
# State-changing transaction (returns a TxResult)
tx_result = ctx.call_contract(
    contract_id="CAW3QNEL...",
    function_name="increment",
    args=[]
)
print(f"Transaction settled. Hash: {tx_result.hash}, Return: {tx_result.return_value}")

# Read-only invocation (returns decoded Python value directly)
count = ctx.call_contract(
    contract_id="CAW3QNEL...",
    function_name="get_count",
    args=[],
    read_only=True
)
print(f"Current counter value: {count}")
```

#### Typed Contract Clients
For a more Pythonic Developer Experience (DX), you can wrap a contract ID in a typed client to call functions as if they were native methods:
```python
client = ctx.contract("CAW3QNEL...")

# Calls are validated against the on-chain contract spec at runtime
tx_result = client.increment()
count = client.read.get_count()          # view call
```

#### Async Calls (`aio`)
To run calls concurrently without blocking the main event loop:
```python
client = ctx.contract("CAW3QNEL...")

# Async state-change
tx_result = await client.aio.increment()

# Async view
count = await client.aio.read.get_count()
```

---

### 2. `HiveClient`
The client wrapper for the on-chain Hive Registry directory. Used for discovering other agents and registering capabilities.

```python
from mycelium import AgentContext, HiveClient

ctx = AgentContext(".mycelium/wallet.json")
hive = HiveClient(ctx)

# Register capabilities on-chain
hive.register(
    unique_name="price_oracle_beta",
    capability_tags=["market-data", "price-feed", "stellar-xlm"],
    endpoint="https://oracle.mycelium-agents.sh/api"
)

# Resolve partner details (returns details dict: address, capabilities, endpoint, reputation)
partner = hive.resolve_agent("data_validator_alpha")
print(f"Partner G-Address: {partner['public_key']}")
print(f"Partner Endpoint: {partner['endpoint']}")
print(f"Partner Reputation Score: {partner['reputation']}")
```

---

### 3. `EscrowPaymentRouter`
Implements the x402 Machine-to-Machine (M2M) payment specification, allowing conditional escrows to be created and claimed.

```python
from decimal import Decimal
from mycelium import AgentContext, EscrowPaymentRouter

ctx = AgentContext(".mycelium/wallet.json")
router = EscrowPaymentRouter(ctx)

# Create an escrow (locks XLM tokens until a hash proof is resolved)
task_hash = b"\x00" * 32  # SHA-256 hash of task criteria
escrow_address = router.create_locked_escrow(
    provider_id="GD...",         # Service provider's G-address
    amount_xlm=Decimal("15.5"),   # Amount of XLM to escrow
    task_hash=task_hash
)

# Release funds (submitted by the provider once the task is complete, passing proof)
verification_proof = b"secret-task-result-bytes"
router.release_funds(escrow_address, verification_proof)

# Multi-agent (swarm) split: divide the locked bounty across N recipients by
# basis points (must sum to 10000). Amounts are computed to sum exactly to the
# locked amount, and the escrow re-checks the balance on release.
router.split_release(
    escrow_address,
    [("GD...prov1", 6000), ("GD...prov2", 4000)],
    verification_proof,
)
```

---

### 4. `JobBoardClient` — Sovereign Job Boards
Post tasks on-chain; single agents or multi-agent swarms claim, prove, and split
the bounty. Builds on `EscrowPaymentRouter` (the bounty is locked in an escrow at
post time) and the Hive Registry (swarm discovery). Every method is a real
Soroban call against the deployed `JobBoard` contract (`job_board_contract.py`).

```python
from decimal import Decimal
import hashlib
from mycelium import AgentContext, JobBoardClient

ctx = AgentContext(".mycelium/wallet.json")
board = JobBoardClient(ctx, board_address="CJB...")

spec = "ipfs://Qm...task-spec"
spec_hash = hashlib.sha256(spec.encode()).digest()

# Poster: lock a 5 XLM bounty and post the job.
job_id = board.post_job(spec_uri=spec, spec_hash=spec_hash, bounty_xlm=Decimal("5"), mode="single")

# Agent: claim, then (after doing the work) submit a proof that hashes to spec_hash.
board.claim_job(job_id)
board.submit_proof(job_id, spec.encode())

# Poster/agent: release the bounty (single payout, or N-way swarm split) and close.
board.finalize(job_id, spec.encode())

# Swarm mode: members join with agreed shares (sum to 10000 bps) before submit.
# board.join_swarm(job_id, capability_tag="vision", share_bps=6000)
```

Read-only helpers (`board.list_open_jobs(status="open")`, `board.get_job(id)`,
`board.get_swarm(id)`, `board.job_count()`) simulate with no fee or wallet.

---

## 🤖 AI Framework Integration Adapters

Agents can expose on-chain functions as tools to LLM models. The SDK abstracts the serialization and execution.

### 1. LangGraph / LangChain Tool Integration
Expose any smart contract function as a standard LangChain `@tool` decoration:

```python
from langchain_core.tools import tool
from mycelium import AgentContext, U64

context = AgentContext(".mycelium/wallet.json")

@tool
def execute_counter_increment() -> str:
    """
    Increments the global on-chain counter by 1. 
    Use this tool whenever the user asks to increase the count.
    """
    try:
        # Wrap arguments in width-correct DSL types to match contract spec
        tx = context.call_contract(
            contract_id="CAW3QNEL...",
            function_name="increment",
            args=[]
        )
        return f"Increment succeeded. Tx Hash: {tx.hash}"
    except Exception as e:
        return f"Increment failed. Error: {str(e)}"
```

---

### 2. Google Gemini Function Calling
Pass on-chain tools directly to the Gemini API with automatic tool-calling routing:

```python
import google.generativeai as genai
from mycelium import AgentContext, HiveClient

ctx = AgentContext(".mycelium/wallet.json")
hive = HiveClient(ctx)

# 1. Define standard tool matching the required function spec
def lookup_hive_agent(agent_name: str) -> str:
    """
    Look up an agent's on-chain public address and API service endpoint 
    by their unique registry name.
    """
    try:
        info = hive.resolve_agent(agent_name)
        return f"Agent '{agent_name}' found. Address: {info['public_key']}, Endpoint: {info['endpoint']}"
    except Exception:
        return f"Agent '{agent_name}' is not registered in the Hive Registry."

# 2. Start chat with automatic function routing enabled
model = genai.GenerativeModel(
    model_name="gemini-2.0-flash",
    tools=[lookup_hive_agent]
)
chat = model.start_chat(enable_automatic_function_calling=True)

response = chat.send_message(
    "Check if there is a registered agent named 'market_analyst_alpha' and tell me its address."
)
print(response.text)
```

---

## 🔄 Programmatic One-Call Agent Loop

The `run_agent_loop` function simplifies agent scaffolding. It accepts a natural language goal, wires on-chain functions as tools, manages prompt-completion loops, executes tools dynamically, and returns the model's final response:

```python
from mycelium import AgentContext, HiveClient, run_agent_loop, ContractTool

ctx = AgentContext(".mycelium/wallet.json")

final_answer = run_agent_loop(
    goal="Call increment to increase the count, then verify the new total.",
    context=ctx,
    provider="anthropic",        # Or "gemini"
    contract_id="CAW3QNEL...",
    tools=[
        ContractTool(
            function_name="increment",
            description="Call this to increase the counter state. Requires transaction fee."
        ),
        ContractTool(
            function_name="get_count",
            read_only=True,
            description="View the current total counter state. Read-only."
        )
    ],
    hive=HiveClient(ctx),        # Enables 'lookup_partner_agent' tool
    max_steps=5
)

print(f"Agent finished. Response:\n{final_answer}")
```

---

## 🔐 Encrypted Wallets At Rest

Mycelium SDK uses industry-standard cryptography to secure agent private keys:
1. **Key Derivation**: Derive a 256-bit AES key from user passphrase using PBKDF2-HMAC-SHA256 with **600,000 iterations** and a 16-byte random salt.
2. **Cipher**: AES-256-GCM with a 12-byte random initialization vector (nonce).
3. **Storage format**: Plaintext address combined with hexadecimal strings in `.mycelium/wallet.json`.

Plaintext keys are never stored on disk. Decryption keys are loaded into volatile memory and cleaned up immediately after transaction signing.