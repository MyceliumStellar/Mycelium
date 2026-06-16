# MYCELIUM MASTER SPECIFICATION & ARCHITECTURE DESIGN (`claude.md`)
## System Identity & Core Directive
Mycelium is a Python-first framework designed to eliminate the "Rust tax" for smart contract development and agentic orchestration on the Stellar network. It bridges the multi-million developer Python AI ecosystem with Soroban, enabling autonomous, on-chain agents to write logic, compile, deploy, discover, and settle economic transactions natively without low-level systems programming overhead.

---

## 1. System Architecture Overview

```
       +-----------------------------------------------------------------+
       |                      MYCELIUM WEB IDE                           |
       |  [Monaco Editor] <---> [Browser IndexedDB]                      |
       +-------------------------------+---------------------------------+
                                       |
                         GitHub OAuth  | (Git-Backed Architecture)
                                       v
+------------------------+    +-----------------------+    +------------------------+
|  METADATA DATABASE     |    |   MYCELIUM BACKEND    |    |      GITHUB API        |
|  (PostgreSQL/Supabase) |<-->|  (FastAPI / Engine)   |<-->| (User Repositories/    |
|  - Encrypted Tokens    |    |  - Auth Coordinator   |    |  File Tree Persistence)|
|  - User Metadata       |    |  - Compiling Sandbox  |    +------------------------+
+------------------------+    +-----------+-----------+
                                          |
                                          | Trigger Compilation
                                          v
+-----------------------------------------------------------------------------------+
|                           MYCELIUM COMPILER PIPELINE                              |
|   Python Script ---> AST Parsing ---> Soroban Type Validator ---> WASM Codegen    |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          | Generates Target WASM
                                          v
+-----------------------------------------------------------------------------------+
|                         STELLAR SOROBAN NETWORK LAYER                             |
|  +------------------------+  +------------------------+  +---------------------+  |
|  |     TESTNET/MAINNET    |  |     HIVE REGISTRY      |  |  x402 ROUTER / ESCROW |  |
|  | (Agent Smart Contracts)|  | (On-Chain Discovery)   |  | (Agent-to-Agent M2M)|  |
|  +------------------------+  +------------------------+  +---------------------+  |
+-----------------------------------------------------------------------------------+
```

---

## 2. Component 1: The Python-to-Soroban Compiler

### 2.1 Theoretical Framework
Python is dynamically typed and garbage-collected with a heavy runtime footprint. Soroban smart contracts mandate strict execution determinism, static validation, minimal gas costs, and compact WebAssembly (WASM) footprints. 

To bridge this gap, the Mycelium Compiler does not run a python interpreter on-chain. Instead, it compiles a **strictly-typed, restricted semantic subset of Python** directly into optimized WebAssembly intermediate representations or clean, isomorphic Soroban-compatible Rust code that compiles directly to target WASM.

### 2.2 AST Parsing & Type-Mapping System
The compiler leverages Python's native `ast` standard library to generate an Abstract Syntax Tree, validates structural grammar restrictions, and performs explicit static type enforcement.

#### Primitive Typings Mapping
* `int` (with context/decorators) $ightarrow$ Maps cleanly to `i32`, `i64`, `u32`, `u64`, or `i128` based on size hints.
* `str` $ightarrow$ Validated and transformed to native Soroban `String` or `Symbol` (if length $\le 32$ alphanumeric characters).
* `bytes` $ightarrow$ Maps directly to Soroban `Bytes`.
* `bool` $ightarrow$ Maps directly to native `bool`.
* `dict[K, V]` $ightarrow$ Compiled into Soroban native `Map<K, V>`.
* `list[T]` $ightarrow$ Compiled into Soroban native `Vec<T>`.

#### Memory & State Storage Decorators
Soroban demands precise classification of ledger entries to prevent memory-bloat and handle state rentals. Mycelium uses native Python function decorators to define storage lifetimes explicitly:
* `@state.instance`: Maps data directly to the contract instance storage (shared across operations, remains active as long as instance is alive).
* `@state.persistent`: Maps data to individual persistent keys that require separate rental renewals.
* `@state.temporary`: Maps data to ephemeral keys that can expire without blocking contract operation if unrenewed.

### 2.3 Concrete AST Transformation Code Sample
Below is a conceptual layout of the parser loop inside the Mycelium Compiler processing a Python class declaration:

```python
import ast

class MyceliumCompilerVisitor(ast.NodeVisitor):
    def __init__(self):
        self.contract_name = None
        self.state_variables = {}
        self.functions = []

    def visit_ClassDef(self, node):
        # Enforce that the class marks a contract boundary
        has_decorator = any(d.id == 'contract' for d in node.decorator_list if isinstance(d, ast.Name))
        if not has_decorator:
            raise SyntaxError(f"Class '{node.name}' must be decorated with @contract to be compiled.")
        
        self.contract_name = node.name
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        # Track persistent state structures defined at class-level
        if isinstance(node.target, ast.Name):
            var_name = node.target.id
            var_type = ast.unparse(node.annotation)
            self.state_variables[var_name] = {
                "type": var_type,
                "storage_mode": "instance" # Default
            }

    def visit_FunctionDef(self, node):
        # Extract storage scope decorators
        storage_mode = "instance"
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Attribute) and decorator.value.id == 'state':
                storage_mode = decorator.attr
                
        func_meta = {
            "name": node.name,
            "args": [(arg.arg, ast.unparse(arg.annotation)) for arg in node.args.args],
            "returns": ast.unparse(node.returns) if node.returns else "None",
            "storage_mode": storage_mode
        }
        self.functions.append(func_meta)
```

### 2.4 Isomorphic Syntax Example
#### Input Python Source (`agent_contract.py`)
```python
from mycelium import contract, state, Symbol, i128

@contract
class MarketOracleAgent:
    provider: Symbol
    price_feed: i128

    @state.instance
    def initialize(self, owner: Symbol, initial_price: i128):
        self.provider = owner
        self.price_feed = initial_price

    @state.instance
    def update_price(self, caller: Symbol, new_price: i128) -> bool:
        if caller != self.provider:
            return False
        self.price_feed = new_price
        return True

    @state.instance
    def get_price(self) -> i128:
        return self.price_feed
```

#### Compiled Output Target (Soroban Rust Intermediate Layer)
```rust
#![no_std]
use soroban_sdk::{contract, contractimpl, Env, Symbol, i128};

#[contract]
pub struct MarketOracleAgent;

#[contractimpl]
impl MarketOracleAgent {
    pub fn initialize(env: Env, owner: Symbol, initial_price: i128) {
        env.storage().instance().set(&Symbol::new(&env, "provider"), &owner);
        env.storage().instance().set(&Symbol::new(&env, "price_feed"), &initial_price);
    }

    pub fn update_price(env: Env, caller: Symbol, new_price: i128) -> bool {
        let provider: Symbol = env.storage().instance().get(&Symbol::new(&env, "provider")).unwrap();
        if caller != provider {
            return false;
        }
        env.storage().instance().set(&Symbol::new(&env, "price_feed"), &new_price);
        true
    }

    pub fn get_price(env: Env) -> i128 {
        env.storage().instance().get(&Symbol::new(&env, "price_feed")).unwrap()
    }
}
```

---

## 3. Component 2: Mycelium SDK & CLI Framework

The developer workflow and runtime orchestration run completely out of a clean terminal and python package framework.

### 3.1 CLI Command Design Matrix (`mycelium-cli`)
The CLI acts as a command center, wrapping compilation targets, identity setups, and pipeline triggers.

* `mycelium init <project_name>`
  Creates a standard file structure locally:
  ```
  project_name/
  ├── mycelium.toml
  ├── agents/
  │   └── basic_agent.py
  ├── shared/
  └── tests/
  ```
* `mycelium check`
  Performs local AST static evaluation and validates type constraints before committing to a full network compilation cycle.
* `mycelium compile <file.py>`
  Spins up the local validation pipeline, targets the source file, and outputs the optimized optimized WASM binary directly to a local build directory (`build/target.wasm`).
* `mycelium deploy --network testnet`
  Packages the compiled WASM, serializes the transaction into standard Stellar XDR format, uses the local secret key configuration to sign it, sends it to a Stellar Friendbot/Horizon instance, and prints out the resulting contract hash identity.
* `mycelium agent start <file.py> --contract <hash>`
  Spins up an active background runtime execution process matching the local script logic with its on-chain anchor footprint.

### 3.2 The Runtime SDK (`mycelium` pip package)
The SDK provides the programmatic interface for multi-agent loops, standard interface hooks, and automated XDR construction.

```python
from mycelium import AgentContext, StellarNetwork, Symbol

class AutonomousTraderAgent:
    def __init__(self, keypair_path: str):
        # Context automatically establishes network connection and loads key pairs
        self.ctx = AgentContext.from_keypair(keypair_path, network=StellarNetwork.TESTNET)
        self.contract_id = "CC...34"

    def execute_autonomous_loop(self):
        print("[System] Reading on-chain oracle parameters...")
        # SDK handles mapping dictionary payload -> Soroban XDR Argument Vectors
        current_price = self.ctx.call_contract(
            contract_id=self.contract_id,
            function_name="get_price",
            args=[]
        )
        
        if current_price < 5000:
            print(f"[Decision] Price {current_price} below threshold. Depositing funds...")
            tx_receipt = self.ctx.call_contract(
                contract_id=self.contract_id,
                function_name="update_price",
                args=[Symbol("EXECUTOR"), int(5500)]
            )
            print(f"[Success] Transaction Hash: {tx_receipt.hash}")
```

---

## 4. Component 3: The Hive Registry & Swarm Coordination

The Hive Registry acts as an on-chain global directory where agents do not live in silos; instead, they function as parts of an interconnected neural network ("Hivemind").

```
             +---------------------------------------------+
             |           MYCELIUM HIVE REGISTRY            |
             |           (On-Chain Smart Contract)         |
             +----------------------+----------------------+
                                    |
          +-------------------------+-------------------------+
          | Look up capability hash | Match & Return Metadata |
          v                                                   v
+------------------------+                          +------------------------+
|      BUYER AGENT       |=========================>|     SERVICE AGENT      |
| "Need Market Analysis" |  x402 Microtransaction   | "Executes Computation" |
+------------------------+                          +------------------------+
```

### 4.1 Registry Smart Contract Interface
The Hive Registry is a master smart contract holding global storage matrices tracking active network capabilities:

```python
from mycelium import contract, state, Symbol, Bytes, Vec, Map

@contract
class MyceliumHiveRegistry:
    # Storage maps agent public key identifier to their functional attributes metadata
    # Metadata includes capability hash, service endpoint strings, and reputation score
    registry: Map[Bytes, Map[Symbol, Bytes]]

    @state.persistent
    def register_agent(self, agent_id: Bytes, capability_hash: Bytes, operational_uri: Bytes):
        manifest = Map()
        manifest[Symbol("capability")] = capability_hash
        manifest[Symbol("endpoint")] = operational_uri
        manifest[Symbol("reputation")] = Bytes(b"100") # Base status initialization
        
        self.registry[agent_id] = manifest

    @state.instance
    def discover_capability(self, target_capability: Bytes) -> Vec[Bytes]:
        matched_agents = Vec()
        for agent_id in self.registry.keys():
            manifest = self.registry[agent_id]
            if manifest[Symbol("capability")] == target_capability:
                matched_agents.append(agent_id)
        return matched_agents
```

### 4.2 Cryptographic Capabilities Manifest Payload
When registering, agents push a cryptographic manifest signature. This JSON metadata mapping describes exactly what an agent can perform, represented as a SHA-256 string for verified verification checks:

```json
{
  "agent_identity": "GD...789",
  "semantic_tags": ["market-intelligence", "price-prediction", "xlm-forecast"],
  "api_interface_spec_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "base_rate_per_invocation_xlm": "0.1000000"
}
```

---

## 5. Component 4: Agent-to-Agent Commerce & x402 Infrastructure

Autonomous interaction requires native value exchange. Mycelium embeds machine-to-machine payment rails into the network stack using native Stellar primitives and extending the x402 machine payment design principles.

### 5.1 Asset Settlement Mechanism
All machine services require continuous micropayments. Transactions utilize native assets (XLM) or stable tokens (USDC) circulating on the Stellar asset engine. The SDK incorporates instant settlement patterns without complex payment channel construction:

1. **Request Phase**: Agent A requests an analytical query from Agent B.
2. **Escrow Phase**: Agent A seals the required fee tokens inside an ephemeral Mycelium Escrow contract.
3. **Fulfillment Phase**: Agent B processes the data, signs the cryptographic output payload, and pushes it to the contract layer.
4. **Disbursal Phase**: The contract verifies the signatures match, triggers automated token release directly to Agent B, and delivers the data package identifier to Agent A.

### 5.2 Step-by-Step Payment Token Flow Matrix
```
[Agent A: Buyer] ---> (Locks 0.5 XLM) ---> [Ephemeral Mycelium Escrow Contract]
                                                    |
[Agent B: Provider] ---> (Pushes Signed Result) ----+
                                                    | (Verification Triggers)
                                                    v
                    - 0.5 XLM Disbursed to [Agent B]
                    - Cryptographic Data Packet Key Disbursed to [Agent A]
```

---

## 6. Web IDE Architecture (Approach 1: Git-Backed)

The Mycelium IDE is a browser-native development playground designed to give developers immediate, friction-free access to the compilation toolchain.

### 6.1 Authentication Workflow (GitHub OAuth)
Authentication bypasses classic credential models to directly onboard professional developers:
1. User clicks "Login via GitHub" inside the web IDE.
2. The user is redirected to GitHub authorization portals requesting `repo` and `write:discussion` permissions scopes.
3. GitHub routes back an authentication authorization token callback payload to the Mycelium API Gateway backend.
4. The backend stores the short-lived access authorization token inside a secured database record and returns a signed secure HTTP-only JWT access cookie to the browser interface.

### 6.2 Light Metadata Tracking Schema (PostgreSQL)
Since user code is saved directly into the user's GitHub repositories, the local relational database remains slim and optimized for metadata tracking.

```sql
-- Core User Mapping Table
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    github_user_id BIGINT UNIQUE NOT NULL,
    github_username VARCHAR(255) NOT NULL,
    avatar_url TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Encrypted API Access Token Management Table
CREATE TABLE user_credentials (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    encrypted_github_token TEXT NOT NULL, -- Encrypted using AES-GCM 256
    token_salt BYTEA NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Active User Sessions Tracking Matrix
CREATE TABLE active_workspaces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    current_repository_url TEXT NOT NULL,
    current_active_branch VARCHAR(100) DEFAULT 'main',
    last_synced_commit VARCHAR(40),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
```

### 6.3 Local Client-Side Synchronization Vector
To maximize interface responsiveness and avoid constant blocking API latency penalties, the Web IDE implements an asynchronous data sync engine:

1. **State Persistence Loop**: As a developer types in the Monaco Editor viewport, changes are continuously written locally to the browser's high-capacity **IndexedDB Engine**.
2. **Commit Pipeline Strategy**: When the user triggers an internal checkpoint ("Save Project" or `Ctrl+S`), the IDE packages the changed working file tree, generates a commit delta, and fires a non-blocking payload directly through the Mycelium API Backend.
3. **GitHub API Proxying**: The backend decrypts the user's `encrypted_github_token`, loads the active repository branch interface via standard GitHub REST endpoints (`/repos/{owner}/{repo}/contents/{path}`), creates a clean Git commit, and pushes the code downstream.

### 6.4 Remote Execution Worker Sandboxing
Compilation cannot securely execute directly within raw user web clients due to underlying environment restrictions.
* When a developer clicks **"Compile"**, the local file bundle is sent to a secure, isolated Docker container sandbox managed by the Mycelium backend execution engine.
* The backend invokes the `mycelium-compiler` engine internally against the Python source array, processes the AST nodes, generates the output `target.wasm` artifact, and transmits the resulting base64-encoded WASM byte array back to the web browser interface alongside stdout logs.
* The frontend receives the payload and lets the user deploy it directly to Stellar Testnet via their linked wallet extension.

---

## 7. Development & Grant Milestones Timeline

```
+---------------------------------------------------------------------------------------+
| GARAGE INCUBATION MILESTONE (Core MVP Engine Focus)                                  |
| [X] Custom AST Compiler Pipeline Core Syntax Setup                                    |
| [X] Basic `mycelium-cli` Local Compilation Engine & WASM Output Pipeline              |
| [X] Prototype Local Sandbox Deployment Scripts on Stellar Testnet                    |
+---------------------------------------------------------------------------------------+
                                        |
                                        v
+---------------------------------------------------------------------------------------+
| INCUBATION EXPANSION PHASE (Developer Experience Buildout)                             |
| [ ] GitHub OAuth Authentication Module & PostgreSQL Metadata Tracking Deployment      |
| [ ] Browser Monaco Editor Workspace Layer & Client-Side Sync Matrix Implementation   |
| [ ] Isolated Cloud Compiler Execution Sandbox Implementation                         |
+---------------------------------------------------------------------------------------+
                                        |
                                        v
+---------------------------------------------------------------------------------------+
| GLOBAL NETWORK SCALING PHASE (The Autonomous Swarm Economy)                           |
| [ ] Hive Registry Smart Contract Architecture Deployment on Stellar Testnet          |
| [ ] Multi-Agent Inter-Process SDK Communication Routing Implementation                |
| [ ] x402 Machine-to-Machine Escrow-Backed Commerce Payments Infrastructure Production |
+---------------------------------------------------------------------------------------+