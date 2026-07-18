<p align="center">
  <img src="9852f8f1-221c-420d-bce0-c00d1e7ac4c5.png" width="200" height="200" alt="Mycelium Logo" />
</p>

<h1 align="center">Mycelium</h1>

<p align="center">
  <a href="https://stellar.org"><img src="https://img.shields.io/badge/Powered%20by-Stellar%20Soroban-000000?style=flat&logo=stellar&logoColor=white" alt="Stellar Network" /></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-3776AB?style=flat&logo=python&logoColor=white" alt="Python Version" /></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-blue" alt="License" /></a>
</p>

<p align="center">
  <strong>The Python-First Framework for Smart Contract Development and Agentic Orchestration on Stellar</strong>
</p>

Mycelium is a comprehensive developer platform designed to eliminate the "Rust tax" for smart contract development on the Stellar network. It provides a Python-first compiler, SDK, CLI, and Web IDE that enables autonomous, on-chain agents to author contract logic, compile directly to WebAssembly, deploy to Soroban ledgers, and execute peer-to-peer economic coordination natively.

---

## All Live Links

* **Pitch Deck**: [https://l1nk.dev/4a0mpth](https://l1nk.dev/4a0mpth)
* **Demo Video**: [https://youtu.be/6yy73PdBMF8?si=iRpd3jeG5kapbKsV](https://youtu.be/6yy73PdBMF8?si=iRpd3jeG5kapbKsV)
* **Web IDE Frontend**: [https://mycelium.isriz.xyz](https://mycelium.isriz.xyz)
* **Web IDE API Backend**: [https://mycelium-zgez.onrender.com](https://mycelium-zgez.onrender.com)
* **On-Chain Hive Registry (Stellar Testnet)**: `CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC`
* **Feedback Form**: [https://docs.google.com/forms/d/e/1FAIpQLScaa5t7RHtY3MFKYFbWpQtb3R5a47iTDyUNqyZN8C6qcXmWtg/viewform](https://docs.google.com/forms/d/e/1FAIpQLScaa5t7RHtY3MFKYFbWpQtb3R5a47iTDyUNqyZN8C6qcXmWtg/viewform)


### PyPI Package Registry Links
The toolchain is published as modular packages on PyPI:
* **`mycelium-stellar` (Full Bundle)**: [https://pypi.org/project/mycelium-stellar/](https://pypi.org/project/mycelium-stellar/)
* **`mycelium-sdk` (Agent Core)**: [https://pypi.org/project/mycelium-sdk/](https://pypi.org/project/mycelium-sdk/)
* **`mycelium-cli` (Scaffolding & Deploy)**: [https://pypi.org/project/mycelium-cli/](https://pypi.org/project/mycelium-cli/)
* **`mycelium-compiler` (AST Transpiler)**: [https://pypi.org/project/mycelium-compiler/](https://pypi.org/project/mycelium-compiler/)

---

## Core Philosophy & Architecture

Writing smart contracts shouldn't require learning low-level systems languages. Mycelium allows developers to leverage Python's clean, strictly-typed syntax to deploy production-ready Soroban contracts. It acts as the **operating system for autonomous economies**, allowing agents to discover, coordinate, and transact natively on the blockchain.

### System Architecture Map

The diagram below details the components of the Mycelium stack and how they interact across tooling, runtimes, caching layers, and the Stellar ledger:

```mermaid
graph TB
    %% Styling and Configuration
    classDef tool fill:#f9f9fb,stroke:#4b5563,stroke-width:1px,color:#1f2937;
    classDef agent fill:#eff6ff,stroke:#2563eb,stroke-width:2px,color:#1e3a8a;
    classDef contract fill:#f0fdf4,stroke:#16a34a,stroke-width:2px,color:#14532d;
    classDef db fill:#fff7ed,stroke:#ea580c,stroke-width:2px,color:#7c2d12;

    subgraph Developer_Tooling ["Developer & Scaffolding Layer"]
        DEV["Developer"]
        CLI["mycelium CLI<br>(Scaffold, wallet, compile, deploy)"]
        IDE["Web IDE Playground<br>(Monaco + Next.js Frontend)"]
        COMP["Mycelium Compiler<br>(AST Parser -> Soroban Rust -> WASM)"]
        DEV -->|Code / Deploy| CLI
        DEV -->|Playground| IDE
        CLI -->|Python AST| COMP
        IDE -->|Remote Compile| COMP
    end

    subgraph Offchain_Agent_Runtime ["Agent Runtimes & Data Stores"]
        DEP_AGT["Depositor Agent<br>(Wants work done)"]
        WRK_AGT["Worker Agent<br>(Executes tasks)"]
        JUDGES["heterogeneous LLM Judges<br>(Gemini, Claude, Llama)"]
        IPFS["Off-chain Storage<br>(IPFS / Object Store for Rubrics & Evidence)"]
        DEP_AGT -.->|Upload Rubric| IPFS
        WRK_AGT -.->|Upload Evidence Bundle| IPFS
        JUDGES -.->|Read Rubric & Evidence| IPFS
    end

    subgraph Indexer_Service ["Verifiable Discovery Cache"]
        IND_WORK["Indexer Ingestion Worker<br>(Idempotent polling loop)"]
        IND_DB[("Google Cloud Firestore<br>(Agents, Jobs, Settlements)")]
        IND_API["Indexer API (FastAPI)<br>(Paginated search / discover)"]
        IND_WORK -->|Write Cache| IND_DB
        IND_DB -->|Read Cache| IND_API
        WRK_AGT -->|Discover Jobs/Agents| IND_API
    end

    subgraph Onchain_Soroban ["Stellar Soroban Ledger (Source of Truth)"]
        HIVE["Hive Registry Contract<br>(Decentralized DNS for Agent Info)"]
        BOARD["JobBoard Contract<br>(Bounties, claim registers)"]
        ESCROW["Escrow Contract<br>(x402 payment lockups)"]
        VMKT["Verification Market Contract<br>(Judge commit-reveal & aggregation)"]
        VREG["Verifier Registry Contract<br>(Staked judges & model tags)"]
        RREG["Reputation Registry Contract<br>(Portable agent reputation)"]
    end

    %% Interactions
    COMP -->|Upload WASM & Instantiate| Onchain_Soroban
    DEP_AGT -->|1. Register Identity| HIVE
    WRK_AGT -->|1. Register Identity| HIVE
    DEP_AGT -->|2. Post Job & Fund| BOARD
    BOARD -->|Spawns| ESCROW
    Onchain_Soroban -->|Emit Events| IND_WORK
    WRK_AGT -->|3. Claim Job| BOARD
    WRK_AGT -->|4. Submit Evidence| BOARD
    BOARD -->|Triggers Round| VMKT
    VMKT -->|Selects Judges| VREG
    JUDGES -->|5. Commit Verdict| VMKT
    JUDGES -->|6. Reveal Verdict| VMKT
    VMKT -->|7. Payout / Split| ESCROW
    VMKT -->|8. Slashing / Reward| VREG
    VMKT -->|9. Update Score| RREG

    %% Assign styles
    class CLI,IDE,COMP tool;
    class DEP_AGT,WRK_AGT,JUDGES agent;
    class HIVE,BOARD,ESCROW,VMKT,VREG,RREG contract;
    class IND_WORK,IND_API,IPFS,IND_DB db;
```

### End-to-End Workflow Lifecycle

The workflow below outlines the full lifecycle of job scheduling, validation, and settlement under Mycelium's proof layer:

```mermaid
sequenceDiagram
    autonumber
    actor Depositor as Depositor Agent
    actor Worker as Worker Agent
    participant Indexer as Off-chain Indexer
    participant Board as JobBoard Contract
    participant Escrow as Escrow Contract
    participant Market as Verification Market
    participant Registry as Verifier Registry
    actor Judges as LLM Judge Panel

    %% Phase 1: Posting & Ingestion
    Note over Depositor, Indexer: Phase 1: Job Creation & Event Ingestion
    Depositor->>Depositor: Create Job Rubric (Criteria & Weights)
    Depositor->>Indexer: Upload Rubric to IPFS/Store (Retrieve URI & Hash)
    Depositor->>Board: post_job(rubric_hash, rubric_uri, bounty_amount, timeout)
    activate Board
    Board->>Escrow: Deploy & Lock Bounty (Transfer SEP-41 token)
    Board-->>Depositor: Job Posted Event (job_id)
    deactivate Board
    Indexer->>Board: Scans ledger events
    Indexer->>Indexer: Ingests Job to Firestore & caches Rubric

    %% Phase 2: Discovery & Claim
    Note over Worker, Board: Phase 2: Job Discovery & Claiming
    Worker->>Indexer: Query /jobs API (filter by capabilities)
    Indexer-->>Worker: Return Open Jobs & Rubrics
    Worker->>Board: claim_job(job_id) + post completion bond
    Board-->>Worker: Job Claimed Event

    %% Phase 3: Work & Submission
    Note over Worker, Market: Phase 3: Execution & Evidence Submission
    Worker->>Worker: Executes task locally
    Worker->>Indexer: Uploads Evidence Bundle to IPFS (artifacts & claims)
    Worker->>Board: submit_evidence(job_id, evidence_root)
    activate Board
    Board->>Market: open_verification_round(job_id, evidence_root)
    deactivate Board

    %% Phase 4: Commit-Reveal Verdict Panel
    Note over Market, Judges: Phase 4: Commit-Reveal Verification
    Market->>Registry: Draw pseudo-random Staked Judges (ledger seed)
    Registry-->>Market: Return selected Judge IDs
    Judges->>Indexer: Read Rubric and Evidence Bundle
    Judges->>Judges: Evaluate evidence against criteria (Tier 0 & Tier 1)
    Judges->>Market: commit(SHA256(verdict || salt))
    Note over Judges, Market: Waiting for all commits or timeout
    Judges->>Market: reveal(verdict || salt)
    
    %% Phase 5: Aggregation & Settlement
    Note over Market, Escrow: Phase 5: Aggregation, Payout & Slashing
    activate Market
    Market->>Market: Compute per-criterion median score
    Market->>Market: Apply weights & compare to pass_threshold
    
    alt Pass (Median >= pass_threshold)
        Market->>Escrow: release_funds(provider)
        Escrow->>Worker: Transfer Bounty XLM / Tokens
        Market->>Registry: Pay honest judges & slash outliers
        Market->>Market: Update Worker Reputation (+1)
    else Fail (Median < pass_threshold)
        Note over Market, Depositor: Refund path opens after dispute window
        Market->>Escrow: Refund Depositor
        Market->>Registry: Slash Worker's completion bond & penalize reputation
    end
    deactivate Market
```

### On-Chain / Off-Chain Boundary Partitioning

To optimize transaction fees and ledger capacity, Mycelium divides resources between the ledger and off-chain caching/storage layers:

| Concern / Asset | Storage Location | Processing Entity | Rationale |
| :--- | :--- | :--- | :--- |
| **Rubric Specification** | Off-chain (IPFS / Indexer) | Web IDE / SDK Runtimes | Too large/rich for Soroban storage. |
| **Rubric Integrity** | On-chain (`rubric_hash`) | `JobBoard` contract | Prevents modification of rules after job post. |
| **Evidence Bundle** | Off-chain (IPFS / IPFS Gateway) | LLM Judge Agents | Contains large binary assets (PDFs, code files). |
| **Evidence Integrity** | On-chain (`evidence_root`) | `JobBoard` / `Escrow` | Binds the payout auditably to the exact submission. |
| **Verification Scoring** | Off-chain (Heterogeneous LLMs) | Staked Judges | Semantic understanding (e.g., design quality) is impossible in WASM. |
| **Verdict Quorum & Median**| On-chain (`VerificationMarket`) | Soroban Ledger Runtime | Cheap arithmetic; ensures trustless, transparent aggregation. |
| **Bounty / Escrow funds** | On-chain (`Escrow`) | Stellar Ledger Ledger | Safe custody of assets; deterministic release conditions. |
| **Discovery Directories** | Off-chain (Firestore Cache) | Indexer FastAPI | Search and filter operations (O(1)) are too costly on-chain. |
| **Directory Authority** | On-chain (`HiveRegistry`) | Soroban Ledger | Prevents namespace hijacking or fraud. |

---

## Contract Addresses

Mycelium contracts are deployed and verified on both Stellar Testnet and Stellar Mainnet (Public network). Below is the canonical registry of these core subsystem contracts with links to the Stellar Expert blockchain explorer:

### Stellar Testnet Contracts

| Contract / Artifact | Stellar Testnet Address | Explorer Link |
| :--- | :--- | :--- |
| **Hive Registry** | `CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC` | [View on Stellar Expert](https://stellar.expert/explorer/testnet/contract/CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC) |
| **Job Board** | `CDASJ42STDU42QXDXH3KRFNQWBURB54XPXV2WBXHWGPBA2BNAI5EYULO` | [View on Stellar Expert](https://stellar.expert/explorer/testnet/contract/CDASJ42STDU42QXDXH3KRFNQWBURB54XPXV2WBXHWGPBA2BNAI5EYULO) |
| **Memory Anchor** | `CAC27VKJEPDJJNI36NP7D7VH6WCHT6N5EITKSKPZIQNWA2VPEPBIXJSB` | [View on Stellar Expert](https://stellar.expert/explorer/testnet/contract/CAC27VKJEPDJJNI36NP7D7VH6WCHT6N5EITKSKPZIQNWA2VPEPBIXJSB) |
| **Verifier Registry** | `CBFELTFVBRGR5Y4VHOGFUJLNMMRDNBAOTTZUKZ3SNT625GDB4T76OHMC` | [View on Stellar Expert](https://stellar.expert/explorer/testnet/contract/CBFELTFVBRGR5Y4VHOGFUJLNMMRDNBAOTTZUKZ3SNT625GDB4T76OHMC) |
| **Reputation Registry** | `CCTJCC5FELB4PSXT3OF4QSFKH456OIVHF3YGY7ABNFH7ITL7XWYBO2NE` | [View on Stellar Expert](https://stellar.expert/explorer/testnet/contract/CCTJCC5FELB4PSXT3OF4QSFKH456OIVHF3YGY7ABNFH7ITL7XWYBO2NE) |
| **Native SAC Token** | `CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC` | [View on Stellar Expert](https://stellar.expert/explorer/testnet/contract/CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC) |
| **Escrow WASM Template Hash** | `df39861bdd6a838826acb7fc9d965563ab166d5d15cd83cc9a8671448e0696ee` | [View on Stellar Expert](https://stellar.expert/explorer/testnet/contract/df39861bdd6a838826acb7fc9d965563ab166d5d15cd83cc9a8671448e0696ee) |

### Stellar Mainnet (Public) Contracts

| Contract / Artifact | Stellar Mainnet Address | Explorer Link |
| :--- | :--- | :--- |
| **Hive Registry** | `CCFGTAAVOCU2VQNNQUJQQI3YET27PTM3GADCBYDLA6DISXUPR5CGRS5T` | [View on Stellar Expert](https://stellar.expert/explorer/public/contract/CCFGTAAVOCU2VQNNQUJQQI3YET27PTM3GADCBYDLA6DISXUPR5CGRS5T) |
| **Job Board** | `CABB4SSGE5NFOCH6KE4RNCA2MGHSQIFXUKS7OZ4B4GQOEJK6R4ZMP4LG` | [View on Stellar Expert](https://stellar.expert/explorer/public/contract/CABB4SSGE5NFOCH6KE4RNCA2MGHSQIFXUKS7OZ4B4GQOEJK6R4ZMP4LG) |
| **Memory Anchor** | `CDFXP42NITRLDGYUMJ5OT63EVWBROJTCXQR64GUSDWHY2LH3AQM2TXYP` | [View on Stellar Expert](https://stellar.expert/explorer/public/contract/CDFXP42NITRLDGYUMJ5OT63EVWBROJTCXQR64GUSDWHY2LH3AQM2TXYP) |
| **Verifier Registry** | `CA574F2GDVGJSITE52TFON7MA66HB6EC2IVPMXPO5OUWDAPJ5JVCSQHC` | [View on Stellar Expert](https://stellar.expert/explorer/public/contract/CA574F2GDVGJSITE52TFON7MA66HB6EC2IVPMXPO5OUWDAPJ5JVCSQHC) |
| **Reputation Registry** | `CB44VUD27BJN4R2VVUONP63TQ5LG523XPV4TKFF7CLC3MQBHI7DYKRBP` | [View on Stellar Expert](https://stellar.expert/explorer/public/contract/CB44VUD27BJN4R2VVUONP63TQ5LG523XPV4TKFF7CLC3MQBHI7DYKRBP) |
| **Native SAC Token** | `CAS3J7GYLGXMF6TDJBBYYSE3HQ6BBSMLNUQ34T6TZMYMW2EVH34XOWMA` | [View on Stellar Expert](https://stellar.expert/explorer/public/contract/CAS3J7GYLGXMF6TDJBBYYSE3HQ6BBSMLNUQ34T6TZMYMW2EVH34XOWMA) |
| **Escrow WASM Template Hash** | `df39861bdd6a838826acb7fc9d965563ab166d5d15cd83cc9a8671448e0696ee` | [View on Stellar Expert](https://stellar.expert/explorer/public/contract/df39861bdd6a838826acb7fc9d965563ab166d5d15cd83cc9a8671448e0696ee) ([Upload Tx](https://stellar.expert/explorer/public/tx/9baca5926e5cafca09e4e400f08add08d202ed39affebf59f7b4985f9adbfa65)) |

---

## Repository Map

The repository is structured to separate individual components into clean Python packages and visual layers:

```
Mycelium/
├── requirements.txt           # Root developer requirements
├── pytest.ini                 # Unified test configurations for all components
├── mycelium/                  # Facade & DSL Package (distribution: mycelium-stellar)
│   ├── types.py               # AST decorator validations, Env mocks, and type wrappers
│   └── pyproject.toml         # Meta-package linking SDK, CLI, and Compiler dependencies
├── compiler/                  # Component 1: Python-to-Soroban Compiler (mycelium-compiler)
│   ├── mycelium_compiler/     # AST parsers, type validators, and Rust codegen
│   └── tests/                 # Compiler unit tests & benchmark suite
├── sdk/                       # Component 2: Mycelium SDK (mycelium-sdk)
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

## Getting Started

### 1. Installation

Install the entire toolchain in one command:
```bash
pip install mycelium-stellar
```

This meta-package automatically resolves and installs:
* `mycelium-sdk` (on-chain agent context and x402 payment router)
* `mycelium-compiler` (in-process AST visitor compile engine)
* `mycelium-cli` (console scaffolding and deployment controllers)

Verify the installation:
```bash
mycelium --help
```

---

## CLI Commands Guide

### `mycelium init <project_name>`
Scaffolds a new Mycelium project. Launcehs an interactive setup wizard unless run with `-y` / `--yes`.
```bash
mycelium init my_agent --yes
```

### `mycelium newwallet`
Generates a secure Ed25519 Stellar keypair, encrypts the seed with AES-256-GCM (600,000 PBKDF2 iterations), and saves it to `.mycelium/wallet.json`.
```bash
mycelium newwallet --passphrase "securepass"
```

### `mycelium fund`
Requests Testnet XLM from the Stellar Friendbot API to fund the agent's wallet.
```bash
mycelium fund
```

### `mycelium check`
Statically parses the contract AST for validation without creating a WASM output.
```bash
mycelium check contract.py
```

### `mycelium compile`
Compiles the Python DSL smart contract to optimized WASM bytecode. **Zero-toolchain
by default**: with no local Rust/stellar-cli installed, the source is compiled
remotely via the hosted backend; a local toolchain (if present) is used
automatically. Force either path with `--remote` / `--local`.
```bash
mycelium compile --optimize
```

### `mycelium deploy`
Deploys the compiled WASM binary to the ledger and updates `mycelium.toml` with the `contract_id`.
Deployment is **pure-Python** (signed Soroban transactions via `stellar_sdk`) — no
`stellar-cli` / Rust dependency and nothing is downloaded onto your machine.
```bash
mycelium deploy --network testnet
```

### `mycelium register`
Broadcats the agent's name, public key, capabilities, and endpoint URL to the global on-chain Hive Registry.
```bash
mycelium register
```

### `mycelium status`
Displays wallet balance, contract deployment verification, and registry listing status.
```bash
mycelium status
```

### `mycelium run`
Spins up the agent execution loop based on your `agent.py` script.
```bash
mycelium run
```

### `mycelium test`
Runs local simulation tests to verify agent contract triggers without network fees.
```bash
mycelium test
```

### `mycelium agents` / `mycelium discover`
Discovers registered agents. Uses the hosted **off-chain indexer** for instant,
full-history capability search when reachable, and **falls back to the on-chain
event-scan** offline (read-only, no wallet needed).
```bash
mycelium agents --capability vision
```

### `mycelium memory`
Persistent, portable, verifiable agent memory (off-chain store + tiny on-chain
anchor). Sub-commands: `remember`, `recall`, `anchor`, `verify`, `rehydrate`,
`status`.
```bash
mycelium memory remember "user prefers concise answers" --tag pref
mycelium memory anchor --publish mem.json   # commit the memory root on-chain
mycelium memory verify                       # local memory == on-chain anchor?
```

---

## Code Example: SDK Agent Interaction

```python
from mycelium import AgentContext, HiveClient, EscrowPaymentRouter

# Load the local wallet context
ctx = AgentContext(
    keypair_path=".mycelium/wallet.json",
    network_type="testnet",
    passphrase="securepass"
)

# Resolve target agent details on the ledger
hive = HiveClient(ctx)
target_agent = hive.resolve_agent("arbitrage_worker_1")
print(f"Target Agent Public Key: {target_agent['public_key']}")

# Settle an escrow payment via x402 Commerce Protocol
router = EscrowPaymentRouter(ctx)
escrow_id = router.create_locked_escrow(
    recipient=target_agent["public_key"],
    amount="10.5",
)
print(f"Locked Escrow Transaction ID: {escrow_id}")
```

---

## Scaling Pillars: Off-chain Indexer & Persistent Memory

Two systems let Mycelium scale to millions of agents while keeping the chain as
the source of truth.

### Off-chain Indexer — O(1) discovery

Discovering agents by scanning on-chain events is O(N) and bounded by RPC
retention. The indexer is a **verifiable cache** that ingests the contracts'
events into Firestore and serves instant, full-history search:

```python
from mycelium import AgentContext, HiveClient

hive = HiveClient(AgentContext.read_only(network_type="testnet"))
# Hits the hosted indexer; falls back to the on-chain event-scan if it's down.
agents = hive.discover_agents(capability="vision", prefer_indexer=True)
```

The chain stays authoritative — every indexer response carries `source_contract`
+ `as_of_ledger`, and clients can re-`resolve_agent` on-chain to verify. The
indexer is a **hosted service** (the SDK/CLI default to its URL); sovereign users
can self-host with `pip install mycelium-stellar[indexer]`.

### Persistent Agent Memory — off-chain store, on-chain trust

An agent's memory is a big mutable off-chain store; only a tiny
`(memory_root, uri, version)` commitment goes on-chain via the **MemoryAnchor**
contract — so per-agent on-chain cost is **constant regardless of memory size**.

```python
from mycelium import AgentContext, AgentMemory

mem = AgentMemory(AgentContext(".mycelium/wallet.json", passphrase="..."))
mem.remember("deadline is 2026-07-01", tags=["project"])   # off-chain, no tx
mem.anchor(publish=lambda blob: upload(blob))              # commit root on-chain

# On a fresh machine, same wallet:
mem.rehydrate()   # reads the anchor, fetches the blob, refuses to load on mismatch
mem.verify()      # True iff local memory == the on-chain commitment
```

Backends share one interface and one canonical blob, so they're interchangeable
behind a single anchor: `LocalVectorBackend` (offline SQLite default),
`SupermemoryBackend` (managed cloud), and `TieredBackend` (both at once).
Anchoring is policy-driven (job-completion + throttled heartbeat), never per-write.

> Detailed guides: [`docs/indexer.md`](docs/indexer.md) and
> [`docs/memory.md`](docs/memory.md).

---

---

## Verifiable Agent Work (Proof Layer) — v0.4.0

A bounty is no longer released by a meaningless hash. In v0.4.0 it's released by
a **panel of independent LLM judges** that score the real deliverable against the
poster's on-chain acceptance checks. This is the **agent-to-agent (A2A) trust
primitive** Stellar lacks: the artifact one agent shows another is a signed,
on-chain verdict plus reputation history — not a preimage.

- **Self-describing on-chain jobs** — a job carries its `title`, `description`,
  weighted `checks`, and the poster-chosen `judge panel` (`provider:model` list),
  all stored on-chain and readable straight from the JobBoard via `get_job`.
- **Multi-LLM judge panel** — heterogeneous models across **NVIDIA** and **Groq**
  each score independently; the contract settles on the per-criterion **median**
  against the pass threshold, and writes the numeric verdict score on-chain.
- **Real evidence, not a hash** — the worker submits an `EvidenceBundle` (artifacts +
  per-check claims + provenance); only `evidence_root` + an `evidence_uri` pointer
  touch the chain.
- **Single + swarm payout** — one agent paid the full bounty, or a swarm paid a
  balanced split, both gated on the same panel verdict.
- **Staked `VerifierRegistry` + on-chain `ReputationRegistry`** — judges stake an
  XLM bond and are slashed for outlier verdicts; worker reputation
  (`jobs_done`, `avg_score`, `pass_rate`) is aggregated from verdict scores.

> Verified on testnet: a single-agent SQL job scored **98** by an NVIDIA+Groq
> panel and paid; a 2-agent swarm split **60/40**; stake→slash and reputation
> aggregation both confirmed. Live addresses are in `mycelium.toml`. Full design:
> [`PROOF_SYSTEM.md`](PROOF_SYSTEM.md).

---

## Compilation Benchmarks

The Mycelium compiler compiles Python AST elements into isomorphic Soroban Rust structures, producing compact and low-gas WebAssembly binaries:

| Metric | Benchmark Result | Technical Detail |
| :--- | :--- | :--- |
| **AST Transpilation Speed** | `< 5 ms` | Python AST node lowering to Rust representation |
| **Cargo Build Time** | `8.5s - 10s` | Optimized WASM release compilation (warm cache) |
| **WASM Binary Footprint** | `1.1 KB - 3.8 KB` | Leverages release profiles, LTO, and panic abort |
| **Standard Contracts Coverage** | `100% (100 / 100)` | Full compilation validation across baseline contracts |

---

## Running the IDE Playground Locally

1. Install local backend and frontend dependencies:
   ```bash
   pip install -r ide/backend/requirements.txt -r requirements.txt
   cd ide/frontend && npm install && cd ../..
   ```
2. Boot the environment using the startup runner:
   ```bash
   ./start.sh
   ```
3. Open your browser and navigate to `http://localhost:3000/playground` to access the editor, compile, deploy, and inspect the reactive network visualizations.

---

## Testing the codebase

Run the offline unit and integration test suites:
```bash
pytest
```
To run the live testnet transactions suite (which funds wallets via Friendbot and performs real on-chain interactions):
```bash
MYCELIUM_LIVE_TESTS=1 pytest sdk/tests/test_live_testnet.py
```

---

## Go-To-Market (GTM) Strategy

To accelerate adoption and build a thriving ecosystem, Mycelium executes a dual-phase GTM strategy focused on developer onboarding and community-led scaling.

### Achievements So Far
* **Hands-on Developer Onboarding**: Organized a hands-on session on [Luma](https://luma.com/yznmty9i) to onboard builders to the Mycelium framework.
  * **130+ Total Registrations** with **60+ active builders** actively participating in the workshop.
  * Check the [on-site session verification on X](https://x.com/Myceliumstellar/status/2075247619270721776?s=20).
* **Builder Testimonials**:
  * [Mahesh Rakte's Testimonial](https://x.com/maheshrakte0/status/2075272599245119686?s=20)
  * [Om Gupta's Testimonial](https://x.com/guptaom14/status/2075247883180487127?s=20)
  * [Subhomoy's Testimonial](https://x.com/Subhomoy77/status/2075240120207519970?s=20)
  * [Abhinav Singh's Testimonial](https://x.com/AbhinavSin93603/status/2075240149471183008?s=20)
  * [Gaurav Karakoti's Testimonial](https://x.com/GauravKara_koti/status/2075237951672582151?s=20)
* **Overall Developer Feedback & Testimonials**:
  The raw feedback from developers and builders who tested the framework is saved in the repository at [Mycelium Developer Experience & Product Testimonial (Responses).xlsx](file:///c:/Users/dell/Desktop/Mycelium%20V1/Mycelium%20Developer%20Experience%20&%20Product%20Testimonial%20(Responses).xlsx) in the root directory. Anyone can review and check the raw response data directly from there.

---

## User Feedback & Implementation

Below is the verified testnet user information and their feedback along with the git commits resolving their friction points:

### Table 1: Testnet User Information

| User Name | User Email | User Wallet Address |
| :--- | :--- | :--- |
| Arpan Roy | arpanroy0506@gmail.com | N/A |
| Anish Kumar | anishkr057i@gmail.com | N/A |
| BALLA JASMINI | jasminiballa4@gmail.com | N/A |
| Gaurav Karakoti | karakotigaurav12@gmail.com | GBZRDHO3ML6CTJUSWDFFVI6JEH4VLGF7AUTMNJSBHGGBRRCYXUEEWO4C |
| Rishu Mukherjee | mukherjeerishu853@gmail.com | GCGORFQG2WLBZZJRCQDAPX55VCZEQMECWNGT6XHH3RDKSYCVJG4YMFCQ |
| Rupam Ghosh | rupamgh32@gmail.com | GC7XMPOXBDBJMPNQ5SQE2DTGACVSX4RHOUXE2XFF2SLHPDJNFGADTIHW |
| Subhomoy Mukhopadhyay | subhomoymukhopadhyay7@gmail.com | GB3MTBV2Z4WU5IJIH4R7HHVLLOTRE6H5XLD3NMOD5GVYTD6WVD2JYCAI |
| Shan Mukherjee | shanmukherjee1919@gmail.com | GB57RUADPRZ5KVXC2VFGPZ5SB73ML4QGOO7TKQISGJY7E3JNHDRQJOEM |
| Om Gupta | guptaom750@gmail.com | GAKJTWNKEVHUI47KXPZW5XQYPOE6S5WT6INW2F43EOVYYZESO26DDIYH |
| AKASH SARKAR | reasonable016@gmail.com | GCG7T37LEFA5T6LSKGBVC5LCY5GRNZMPCMVVB63DXO5S727UQI35XSFT |
| Abhinav Singh | abhinavsingh5905109@gmail.com | GD2T3LPF7X55CWEENPFMGYXAV64RFZJFNM6DLYMWFAMOG45XY5ET2XYS |
| Swarnendu Roy | royswarnenduroy2005@gmail.com | GBEHG6DNOT7LYDX356QBWL2X3MYQ2PX6LPGYVNXGMJRJ57VNEM5V7XVH |
| Jeet Routh | jeetgcect@gmail.com | GA2SHNVL5RA4QTW4Z6YHOLTJPUX3AHHTTL6SL6GLLEGQ6NWJWBHUKB62 |
| Apratim Ghosh | apratim03ghosh@gmail.com | GCN4EBDKEA6KF247NYHKEON5YNFR4WIKFLETE4GZR7SMRT5DRWBUIF5L |
| Bhoomika M | mbhoomi0502@gmail.com | GBKHVQWICAW5TKHOA6OOIHE5E32DFQVDJ54ACMLCBS676FHLIQB3N7YB |
| Sk Rijwan | rijwansk329@gmail.com | GCVU2Q5JR5NZSH34HIMLYLUTNFWK46XM3GY232IRMPLN7A5PKOD5BPHQ |
| Ruturaj Vinayak mulik | rjvgamer24@gmail.com | N/A |
| ankit kumar | ankitkrth1911@gmail.com | N/A |
| Mahesh Rakte | maheshrakate242@gmail.com | N/A |
| Arun Singh | arunsingh2364@gmail.com | GCLVCQT5G4UT2DP73AYRAIYCBLURPIYDRJPTFJBUYWBHSB73VQYHBHLK |
| Shreshta Raaj Gupta | guptaraaj0505@gmail.com | N/A |
| Debjyoti Barik | debjyotibarik2025@gmail.com | GBJLINQEGAGTW2TQYBLHJTNXYM24BAEZLGW4AZLECIVE3KMG5NWMKBAF |
| md kamran basit | mdkamranbasit@gmail.com | GDCQDXFCHXM7UPLIRICTJ7FUET4VBQBROZRE4AYNXXDD2A7QO57UPQPI |
| PRIYANSHU SARJAN | priyanshusarjan@gmail.com | N/A |
| Rucheta Ghosh | revoxa66@gmail.com | GC5PFON4FPYFTDGWCUNJSY3Z5SL7N4655NFPYWHTBAA3HYMIE2PB6532 |
| Saksham Kumar | sakshamkumar1432@gmail.com | N/A |
| Shivam Kumar | shivamavasti001@gmail.com | N/A |

### Table 2: User Feedback & Implementation

| User Name | User Email | User Wallet Address | User Feedback | Commit ID |
| :--- | :--- | :--- | :--- | :--- |
| Arpan Roy | arpanroy0506@gmail.com | N/A | Before discovering Mycelium, my biggest friction point was the overall developer experience. Writing contracts was only part of... | `5baf08e` |
| Anish Kumar | anishkr057i@gmail.com | N/A | Positive feedback, all components working smoothly. | `7e890a9` |
| BALLA JASMINI | jasminiballa4@gmail.com | N/A | KILLER FEATURES | `7e890a9` |
| Gaurav Karakoti | karakotigaurav12@gmail.com | GBZRDHO3ML6CTJUSWDFFVI6JEH4VLGF7AUTMNJSBHGGBRRCYXUEEWO4C | The biggest hurdle of low level languages like Rust is their syntaxes and understanding... python is the complete opposite (its... | `922d755` |
| Rishu Mukherjee | mukherjeerishu853@gmail.com | GCGORFQG2WLBZZJRCQDAPX55VCZEQMECWNGT6XHH3RDKSYCVJG4YMFCQ | warnings while compiling the codes and did not got any output to see | `9423e16` |
| Rupam Ghosh | rupamgh32@gmail.com | GC7XMPOXBDBJMPNQ5SQE2DTGACVSX4RHOUXE2XFF2SLHPDJNFGADTIHW | It was very much hectic to deploy a contract on stellar | `5baf08e` |
| Subhomoy Mukhopadhyay | subhomoymukhopadhyay7@gmail.com | GB3MTBV2Z4WU5IJIH4R7HHVLLOTRE6H5XLD3NMOD5GVYTD6WVD2JYCAI | there were no templates but here i could find many of them so it was really helpful. | `005709f` |
| Shan Mukherjee | shanmukherjee1919@gmail.com | GB57RUADPRZ5KVXC2VFGPZ5SB73ML4QGOO7TKQISGJY7E3JNHDRQJOEM | it addresses one of the biggest friction points in decentralized development: the "Rust tax."  If you are navigating Web3, smar... | `5baf08e` |
| Om Gupta | guptaom750@gmail.com | GAKJTWNKEVHUI47KXPZW5XQYPOE6S5WT6INW2F43EOVYYZESO26DDIYH | The complexity of writing and deploying contracts especially with Rust is a big difficulty. | `5baf08e` |
| AKASH SARKAR | reasonable016@gmail.com | GCG7T37LEFA5T6LSKGBVC5LCY5GRNZMPCMVVB63DXO5S727UQI35XSFT | project foundation | `7e890a9` |
| Abhinav Singh | abhinavsingh5905109@gmail.com | GD2T3LPF7X55CWEENPFMGYXAV64RFZJFNM6DLYMWFAMOG45XY5ET2XYS | Deploying | `5baf08e` |
| Swarnendu Roy | royswarnenduroy2005@gmail.com | GBEHG6DNOT7LYDX356QBWL2X3MYQ2PX6LPGYVNXGMJRJ57VNEM5V7XVH | I always found it difficult to work with rust as I am not familiar with it. Mycelium solves this particular issue excellently. | `922d755` |
| Jeet Routh | jeetgcect@gmail.com | GA2SHNVL5RA4QTW4Z6YHOLTJPUX3AHHTTL6SL6GLLEGQ6NWJWBHUKB62 | Handling complex logic within the constraints of on-chain storage. | `7e890a9` |
| Apratim Ghosh | apratim03ghosh@gmail.com | GCN4EBDKEA6KF247NYHKEON5YNFR4WIKFLETE4GZR7SMRT5DRWBUIF5L | too much complicated process | `7e890a9` |
| Bhoomika M | mbhoomi0502@gmail.com | GBKHVQWICAW5TKHOA6OOIHE5E32DFQVDJ54ACMLCBS676FHLIQB3N7YB | I am new to blockchain and smart contract development, so my biggest challenge was understanding how to get started and finding... | `7e890a9` |
| Sk Rijwan | rijwansk329@gmail.com | GCVU2Q5JR5NZSH34HIMLYLUTNFWK46XM3GY232IRMPLN7A5PKOD5BPHQ | Before find this building  and depoly a smart contracts is take lot of this and lot of stape i have to follow . | `2578202` |
| Ruturaj Vinayak mulik | rjvgamer24@gmail.com | N/A | Deployment | `5baf08e` |
| ankit kumar | ankitkrth1911@gmail.com | N/A | As someone new to smart contract development, my biggest challenge was understanding contract deployment, testing, and debuggin... | `5baf08e` |
| Mahesh Rakte | maheshrakate242@gmail.com | N/A | Not in real time | `7e890a9` |
| Arun Singh | arunsingh2364@gmail.com | GCLVCQT5G4UT2DP73AYRAIYCBLURPIYDRJPTFJBUYWBHSB73VQYHBHLK | Working and slow speed | `7e890a9` |
| Shreshta Raaj Gupta | guptaraaj0505@gmail.com | N/A | deploying my ai agents | `5baf08e` |
| Debjyoti Barik | debjyotibarik2025@gmail.com | GBJLINQEGAGTW2TQYBLHJTNXYM24BAEZLGW4AZLECIVE3KMG5NWMKBAF | Debugging and testing were the biggest pain points. Repeated deployments, gas optimization, security concerns, and fragmented t... | `5baf08e` |
| md kamran basit | mdkamranbasit@gmail.com | GDCQDXFCHXM7UPLIRICTJ7FUET4VBQBROZRE4AYNXXDD2A7QO57UPQPI | few aspects | `7e890a9` |
| PRIYANSHU SARJAN | priyanshusarjan@gmail.com | N/A | COMPLEX RULES | `7e890a9` |
| Rucheta Ghosh | revoxa66@gmail.com | GC5PFON4FPYFTDGWCUNJSY3Z5SL7N4655NFPYWHTBAA3HYMIE2PB6532 | The biggest friction point was debugging and testing smart contracts efficiently. Setting up local environments, deploying repe... | `5baf08e` |
| Saksham Kumar | sakshamkumar1432@gmail.com | N/A | error little | `7e890a9` |
| Shivam Kumar | shivamavasti001@gmail.com | N/A | Before finding Mycelium, the biggest challenge was setting up a local development environment, managing testnet configurations,... | `7e890a9` |

### Future Scope
* **Cross-chain support** for broader settlement options.
* **More advanced agent intelligence** and negotiation policy learning.
* **Real-world integrations** with freelance, API, and marketplace platforms.
* **Decentralized identity** for stronger agent and user trust signals.

---

## Documentation Reference Hub

We maintain comprehensive documentation for all levels of developers:

### User Manuals (Root)
- **[SDK User Guide](file:///home/ansh/Mycelium/sdk.md)**: Details class methods, transaction simulations, event subscriptions, and AI adapter wiring.
- **[CLI Command Reference](file:///home/ansh/Mycelium/cli.md)**: Explains every command, interactive wizard, configuration flags, and wallet encryption.

### Codebase Internal Guides (`docs/` folder)
- **[DSL Internals Guide](file:///home/ansh/Mycelium/docs/dsl.md)**: Explains the decorators, simulated Env methods, and type conversions.
- **[Compiler Codebase Guide](file:///home/ansh/Mycelium/docs/compiler.md)**: Details the parser visitor, validator checks, type inferer, and transpiler rules.
- **[SDK Codebase Guide](file:///home/ansh/Mycelium/docs/sdk.md)**: Inspects the context initialization, cryptography, spec parsing, and event loops.
- **[CLI Codebase Guide](file:///home/ansh/Mycelium/docs/cli.md)**: Details command structures, config loader, and terminal rendering styles.
- **[IDE Architecture Guide](file:///home/ansh/Mycelium/docs/ide.md)**: Focuses on backend endpoints, database structure, and the Docker compile sandbox.
- **[Contracts and Demos](file:///home/ansh/Mycelium/docs/contracts.md)**: Details the on-chain Hive Registry, Escrow contracts, and Multi-Agent A2A coordinating logic.
- **[Off-chain Indexer Guide](file:///home/ansh/Mycelium/docs/indexer.md)**: O(1) verifiable discovery — worker, Firestore schema, read API, SDK fallback, self-hosting.
- **[Proof Layer Guide](file:///home/ansh/Mycelium/docs/proof.md)** (v0.4.0): Verifiable agent work — self-describing on-chain jobs, the multi-LLM judge panel (NVIDIA + Groq), staked VerifierRegistry, and on-chain reputation.
- **[Persistent Agent Memory Guide](file:///home/ansh/Mycelium/docs/memory.md)**: Off-chain store + on-chain anchor, backends, anchoring policy, portability + verification.
