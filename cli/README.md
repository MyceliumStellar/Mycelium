# Mycelium CLI

The Mycelium CLI (`mycelium` command-line tool) is the developer command center for the Mycelium framework. It provides interactive scaffolding, local wallet/keypair management, contract checking and compilation, Soroban blockchain deployments, agent directory registration in the Hive registry, and execution runners for autonomous agent loops.

---

## 🚀 Installation & Setup

Install the CLI toolchain directly from PyPI (packaged within `mycelium-cli` or bundled inside the parent `mycelium-stellar` wrapper):

```bash
pip install mycelium-cli
```

Verify that the installation was successful by running:
```bash
mycelium --help
```

---

## ⚙️ Configuration Reference (`mycelium.toml`)

All Mycelium CLI operations run relative to a project root containing a `mycelium.toml` file. This configuration serves as the single source of truth for the local agent and its corresponding on-chain contract.

```toml
[project]
name = "sentinel_agent"
version = "0.1.0"
author = "Developer"

[agent]
framework = "gemini"             # Options: "langgraph" | "gemini" | "anthropic" | "custom"
model = "gemini-2.0-flash"        # Target LLM model string
unique_name = "sentinel_alpha"   # Alphanumeric agent registry name

[onchain]
source_contract = "contract.py"  # Path to smart contract source file
target_wasm = "build/contract.wasm" # Output binary path
network = "testnet"             # Default ledger target: "testnet" | "mainnet"
contract_id = "CC..."            # Automatically populated after deployment
wallet_public_key = "GD..."      # Automatically populated after deployment

[registry]
hive_registry_address = "CCQ..." # Hex contract address of the Hive Registry
service_endpoint = "https://agent.sentinel.mycelium.sh" # Agent API URL
capabilities = ["data-analysis", "stellar-arbitrage"] # List of capability tags
```

---

## 🛠️ Complete CLI Command Reference

### 1. `mycelium init`
Scaffolds a new Mycelium project from scratch. It launches an interactive setup wizard that prompts you for project properties.

* **Syntax**:
  ```bash
  mycelium init <project_name> [options]
  ```
* **Interactive Wizard Options**:
  - **AI Core Framework**: Select from `langgraph`, `gemini`, `anthropic`, or `custom`.
  - **Target LLM Model**: Pick from recommended defaults or input a custom string.
  - **Unique Name**: Choose a registry name (regex validated against `^[a-zA-Z0-9_]{3,30}$`).
* **Flags**:
  - `--yes` / `-y`: Skip all interactive questions and initialize using standard default configurations.
  - `--force` / `-f`: Overwrite the destination directory if it already exists.

### 2. `mycelium newwallet`
Generates a new secure Stellar keypair (Ed25519) and saves it to `.mycelium/wallet.json`.

* **Syntax**:
  ```bash
  mycelium newwallet [options]
  ```
* **Security Details**:
  - The secret seed is encrypted at rest using PBKDF2-HMAC-SHA256 (600,000 iterations) + AES-256-GCM.
  - Prompts securely for an encryption passphrase.
  - Filesystem permissions on `.mycelium/wallet.json` are automatically restricted to `0600` (read/write by owner only).
* **Flags**:
  - `--passphrase <text>`: Provide the encryption passphrase directly (convenient for automated environments).
  - `--force`: Force generation, overwriting any existing wallet configuration.

### 3. `mycelium compile`
Parses and compiles a Python-DSL contract file into a WebAssembly contract binary.

* **Syntax**:
  ```bash
  mycelium compile [source_file] [options]
  ```
* **Flags**:
  - `--output <path>` / `-o <path>`: Specify the output WASM file path (defaults to `build/contract.wasm`).
  - `--optimize`: Enable maximum optimization passes (release profile, targeting size reduction).

### 4. `mycelium check`
Performs static evaluation and type verification on a contract script without generating a WASM binary. Useful for checking syntax in IDEs, git pre-commit hooks, or CI pipelines.

* **Syntax**:
  ```bash
  mycelium check [source_file]
  ```

### 5. `mycelium deploy`
Deploys the compiled WASM binary directly to Stellar/Soroban.

* **Syntax**:
  ```bash
  mycelium deploy [options]
  ```
* **Behaviors**:
  - **Testnet**: Checks the balance. If the balance is zero, the CLI automatically requests funds from the Stellar Friendbot API, waits for ledger confirmation, and broadcasts the deployment transaction.
  - **Mainnet**: Asserts the wallet has a minimum balance of `5 XLM` (to satisfy reserves). If insufficient, it halts with an error and displays the public key.
  - On success, updates `contract_id` and `wallet_public_key` in `mycelium.toml`.
* **Flags**:
  - `--network <name>`: Override the network target (`testnet` or `mainnet`).
  - `--wasm <path>`: Override the WASM file path to deploy.

### 6. `mycelium register`
Submits a signed transaction to the global Hive Registry mapping your agent's configuration parameters.

* **Syntax**:
  ```bash
  mycelium register [options]
  ```
* **Details**:
  - Packages the agent name, service endpoint, public address, and the SHA-256 hash of capability tags.
  - Verifies that local keys match the owner keys if updating an existing registration.

### 7. `mycelium status`
Displays the comprehensive deployment and configuration status of the active project in a single screen.

* **Syntax**:
  ```bash
  mycelium status
  ```
* **Output Fields**:
  - **Wallet Address**: G-address extracted from local wallet config.
  - **Wallet Balance**: Balance retrieved from Horizon RPC.
  - **Network**: Deployed target network passphrase identifier.
  - **Contract Deployment**: Verification status of the contract ID on the ledger.
  - **Registry Entry**: Name verification, registration state, reputation score, and API endpoint details.

### 8. `mycelium fund`
Explicitly requests Friendbot funding for the agent's wallet. Used to top up testnet gas balances.

* **Syntax**:
  ```bash
  mycelium fund [options]
  ```
* **Flags**:
  - `--amount <number>`: Request a specific amount (if supported by network node limits).

### 9. `mycelium call`
Invokes an on-chain contract function directly from your terminal.

* **Syntax**:
  ```bash
  mycelium call <function_name> [args...] [options]
  ```
* **Details**:
  - Automatically maps plain argument strings to the correct Soroban type based on the contract specification.
* **Flags**:
  - `--read-only`: Execute as a simulate-only view invocation (free, does not require passphrase or signature).
  - `--contract <id>`: Override the target contract ID.

### 10. `mycelium resolve`
Queries the on-chain Hive Registry to resolve details of another agent by its name.

* **Syntax**:
  ```bash
  mycelium resolve <agent_name>
  ```

### 11. `mycelium pay`
Triggers an agent-to-agent XLM settlement payment. It resolves the destination agent's wallet address from the registry.

* **Syntax**:
  ```bash
  mycelium pay <recipient_name_or_address> <amount_xlm>
  ```

### 12. `mycelium events` / `mycelium logs`
Streams on-chain event topics emitted by the agent's smart contract.

* **Syntax**:
  ```bash
  mycelium events [options]
  ```
* **Flags**:
  - `--contract <id>`: Override the contract ID to monitor.
  - `--start-ledger <number>`: Begin streaming historical events from a specific ledger sequence.

### 13. `mycelium doctor`
Runs a suite of sanity checks to verify the state of your local toolchain:
1. Asserts `stellar-cli` is present on your system path.
2. Checks if local cargo/wasm targets are properly configured.
3. Tests network connectivity and latency to Horizon and Soroban RPC nodes.
4. Identifies version mismatches and prints corrective shell actions.

* **Syntax**:
  ```bash
  mycelium doctor
  ```

### 14. `mycelium run`
Spins up the agent's execution loop (`agent.py`) in your terminal, pre-loading context configurations, wallet files, and contract IDs from the project directory.

* **Syntax**:
  ```bash
  mycelium run [options]
  ```
* **Flags**:
  - `--steps <number>`: Limit the maximum number of steps the LLM loop is permitted to run.

### 15. `mycelium test`
Performs a simulation dry-run of the agent loop. It intercepts all state-changing contract calls, executes them via simulation, logs estimated resource fees, and returns without signing or broadcasting transactions.

* **Syntax**:
  ```bash
  mycelium test
  ```

### 16. `mycelium job` (Proof Layer — v0.4.0)
Posts, performs, judges, and settles verifiable bounties. A job is self-describing
on-chain (title, description, weighted checks, judge panel); release is gated on a
multi-LLM panel verdict, not a hash.

* **Syntax**:
  ```bash
  # Post a self-describing bounty + judge panel
  mycelium job post \
    --title "Write a sales-report SQL query" \
    --description "Aggregate revenue by region, last 12 months." \
    --check correct:70:"returns correct rows" \
    --check style:30:"readable, indexed" \
    --judge-model nvidia:meta/llama-3.1-70b \
    --judge-model groq:llama-3.3-70b \
    --threshold 75

  mycelium job do <job_id> --model groq:llama-3.3-70b   # worker produces + submits evidence
  mycelium job judge <job_id>                            # run the job's panel → verdict → settle
  mycelium job models --provider nvidia                  # list available judge/worker models
  mycelium job status <job_id>                           # on-chain title/checks/panel/score
  ```
* **Details**: `--check` is `id:weight:text` (repeatable); `--judge-model` is
  `provider:model` (repeatable). A single agent is paid the full bounty on a
  passing verdict; a swarm is paid a balanced split — both gated on the same panel.

### 17. `mycelium verifier` (Staked judge pool — v0.4.0)
Manages the `VerifierRegistry`: judges stake an XLM bond to become eligible and are
slashed for outlier verdicts; per-judge accuracy (verifier reputation) is tracked.

* **Syntax**:
  ```bash
  mycelium verifier register --model nvidia:meta/llama-3.1-70b
  mycelium verifier stake 100            # lock an XLM bond
  mycelium verifier info <address>       # stake, jobs judged, accuracy, active
  mycelium verifier eligible <model>     # judges staked + tagged for a model
  mycelium verifier slash <address>      # market-only: penalize an outlier verdict
  mycelium verifier accuracy <address>   # verifier reputation (within-tolerance rate)
  ```

---

## 🔐 Environment Variables

* `MYCELIUM_DECRYPT_KEY`: Set this env variable to bypass interactive wallet decryption password prompts. Essential for CI/CD and non-interactive workflows.
* `MYCELIUM_CONTRACT_ID`: Override default contract target.
* `STELLAR_NETWORK`: Overrides default network selection (`testnet` / `mainnet`).
