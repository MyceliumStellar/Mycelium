# Mycelium — Improvements, Memory Architecture & Scaling Roadmap

This document maps out the current state, shipped milestones, and future roadmap of the Mycelium framework.

---

## 1. What Has Shipped (Fully Implemented)

All major architectural components of the core developer workflow are fully implemented and test-covered:

| Area | Shipped Capabilities |
|---|---|
| **Model Discovery** | `mycelium_sdk/models.py` — Dynamic discovery of models from Gemini, Anthropic, OpenAI, and local Ollama servers by API key/URL. |
| **Marshalling & Decoupling** | Context-aware marshalling that maps plain Python types directly to `SCVal` widths based on on-chain specs. Handled via `mycelium_sdk.scval` so developers never touch raw `stellar_sdk` primitives. |
| **Return-Value Decoding** | Full support for decoding returned transaction values (simulation and settled tx meta) under Protocol 23 `TransactionMetaV4` (which standard python libraries fail to decode natively). |
| **Security Hardening** | Encryption of wallets at rest via AES-256-GCM + PBKDF2. Strict filesystem permissions (`0700` for `.mycelium/`, `0600` for `wallet.json`). StrKey-based public key verification. |
| **Packaging & Facade** | Pinned inter-package wheels allowing a single meta-package distribution (`mycelium-stellar`) to pull in the DSL, SDK, CLI, and Compiler coherently. |
| **Command Line Interface (CLI)** | 18 fully implemented CLI commands wrapper (e.g., `init`, `newwallet`, `compile`, `check`, `deploy`, `register`, `status`, `fund`, `call`, `resolve`, `pay`, `events`, `doctor`, `run`, `test`, `agent`, `discover`). |
| **Typed Contract Clients** | Spec-driven clients (`ctx.contract(cid)`) supporting auto-generated sync, async (`.aio`), and read-only (`.read`) interfaces directly reflecting on-chain code structures. |
| **Orchestration Loop** | One-call agent execution helper (`run_agent_loop` + `ContractTool`) to wire Gemini / Anthropic agents to on-chain smart contract methods seamlessly. |

---

## 2. Future Enhancements & Extensions

### 💻 Web IDE Playground Extensions
- **Live Execution Visual Tracer**: Render a real-time reactive graph showing contract state changes, event emissions, and token flows during transaction execution.
- **Multiplayer Workspaces**: Collaborative coding sandbox allowing multiple developers to work together inside the Monaco editor, synced back to a shared GitHub repository.
- **Monaco IntelliSense for DSL**: Custom language server integration providing syntax highlighting, auto-complete, and inline type-checking for Mycelium decorator and typing primitives.
- **Fuzzing & Property Test Generator**: An automated backend worker that parses Python contracts and generates 50+ property-based fuzz tests to run in the sandbox before mainnet deployment.

### ⚙️ Compiler Extensions
- **Generics & Custom Collection Mappings**: Support for user-defined Python generic classes and deeper nesting structures in compiler code generation.
- **Transpiler SSA Gas Optimizer**: A Static Single Assignment (SSA) optimization pass in codegen that performs function inlining, constant folding, and redundant state-access elimination to minimize WASM size and gas fees.
- **Source Maps**: Map the compiled Rust/WASM bytecode operations directly back to Python source line numbers, enabling standard debuggers to trace execution lines.
- **Static Security Linter**: Integrate static analysis checks to catch vulnerabilities (e.g., authorization check omissions, reentrancy vectors, integer overflow/underflow warnings) during `mycelium check`.

### 🎒 SDK Extensions
- **Off-Chain Memory Anchoring**: Full realization of the `AgentMemory` framework integrating local vector databases and decentralized pinning.
- **Gas Fee Sponsorship**: Out-of-the-box support for sponsored reserves and transaction fee delegation, allowing parent wallets or paymasters to sponsor gas costs for user-facing agents.
- **Multi-Signature Orchestration**: Declarative multi-signature flows, allowing agents to collect cryptographic co-signatures off-chain before submitting a proposal.
- **Connection Pooling & Async RPC**: Native async pooling for high-concurrency loops to eliminate GIL-bound transaction execution bottlenecks.

### 🛠️ CLI Extensions
- **Interactive Contract REPL**: An interactive shell (`mycelium repl`) allowing developers to call contract functions and inspect state variables live on a local sandbox node.
- **Local Ledger Node Wrapper**: Wrap the official `stellar-cli` node engine (`mycelium node start`) to run a local developer validator instance in a single command.
- **Agent Templates Library**: Scaffolding presets (e.g., `mycelium init --template arbitrage`, `mycelium init --template dao-member`) to bootstrap complex multi-agent architectures immediately.

---

## 3. Agentic Memory — Off-Chain Memory, On-Chain Commitment

### The Mycelium Pattern
An on-chain agent needs to remember prior decisions, transaction counterparties, and semantic facts across runs and servers. Since Soroban storage is metered, public, and expensive, Mycelium implements an **off-chain memory with an on-chain commitment** model:

```
            ┌────────────────────────┐        ┌──────────────────────────┐
  Agent ──► │  Off-chain memory store │ ◄────► │  On-chain memory anchor   │
            │  (vectors, facts, files)│        │  (hash + URI + version)   │
            └────────────────────────┘        └──────────────────────────┘
                 large, private,                  tiny, public, verifiable,
                 fast, mutable                    portable, access-controlled
```

- **Off-chain (Semantic Memory)**: Episodic memories, conversation logs, and document fragments are stored off-chain in vector/fact databases.
- **On-chain (Memory Anchor)**: A lightweight `MemoryAnchor` contract (compiled from Mycelium DSL) stores a cryptographic hash (`memory_root`), access URI, and ACL. Any agent can query the registry to find another agent's memory anchor and verify its authenticity.

### Memory Tiering
- **Working Memory**: In-process, active memory session.
- **Short-Term/Episodic**: Recalled from recent execution runs.
- **Long-Term/Semantic**: Shard-managed facts, audited and anchored on-chain lazily to minimize transaction fees.

---

## 4. Integration: Supermemory

Mycelium adopts **Supermemory** (the SOTA open-source memory engine for AI agents) as its default off-chain backend:

- **Sovereign Identifiers**: The agent's Stellar public key (`G-address`) serves as the Supermemory `containerTag`, ensuring data isolate-by-default.
- **Verifiability**: Supermemory manages chunking and semantic indexing, while Mycelium constructs and writes the corresponding cryptographic anchor hash on-chain.
- **Zero-Dependency Fallback**: A local `LocalVectorBackend` (SQLite + local embeddings) is maintained to allow developers to build and test agents fully offline.

---

## 5. Scaling to Millions of Agents

To scale agent swarms to millions of participants, Mycelium targets the following bottlenecks:

- **Sequence-Number Management**: Multiple transactions submitted by the same agent key will conflict. We will introduce channel accounts and a local sequence manager to enqueue transactions safely.
- **Sponsored Reserves**: Leverage Stellar's fee-sponsorship primitives to allow platform developers to sponsor the 3 XLM minimum account reserve for new agents.
- **Off-Chain Indexer**: Querying Hive Registry details directly via RPC simulation scales poorly. An off-chain event indexer will cache registry entries and capability tags to provide instant, fee-free O(1) resolution.
- **State Modes**: Support both *Stateless* agents (rehydrating memory from anchors per run, perfect for serverless scale) and *Stateful* agents (holding warm RPC connections for low-latency actions).

---

## 6. Suggested Priority Order for Next Milestones

1. **Author the Escrow Contract**: Write the `escrow_contract.py` DSL contract, compile it, and wire the `create_locked_escrow` logic inside the SDK's `EscrowPaymentRouter` to fully enable x402 payments.
2. **Core Memory Anchoring**: Author the `MemoryAnchor` contract and implement the SDK `AgentMemory` interface with the offline `LocalVectorBackend` (SQLite).
3. **Supermemory Cloud Backend**: Integrate Supermemory behind the `AgentMemory` interface as the default cloud-scale semantic memory.
4. **Adapter Library Packaging**: Move the LangGraph and Gemini adapters from doc examples to real, importable modules within the `mycelium_sdk` package.
5. **Async RPC & Sequence channels**: Develop the sequence manager and pool connections to unlock high-concurrency execution loops.
6. **Marketplace Indexer**: Build the off-chain indexer for capabilities-based search across the global Hive Registry.
