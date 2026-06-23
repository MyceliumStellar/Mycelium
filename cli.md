# Mycelium CLI Command Reference

This document is the complete user manual for the Mycelium CLI (`mycelium` command line suite). The CLI streamlines the lifecycle of smart-agent contracts on Stellar, wrapping compilation, testing, deployment, and registration into a single console experience.

---

## ⚙️ Core Configuration (`mycelium.toml`)

All commands operate relative to a project root containing a `mycelium.toml` file. This configuration acts as the single source of truth for the local agent and its corresponding smart contract.

```toml
[project]
name = "sentinel_agent"
version = "0.1.0"
author = "Developer"

[agent]
framework = "gemini"             # "langgraph" | "gemini" | "anthropic" | "custom"
model = "gemini-2.0-flash"        # Exact model string
unique_name = "sentinel_alpha"   # Alphanumeric agent registry name

[onchain]
source_contract = "contract.py"  # Path to smart contract source file
target_wasm = "build/contract.wasm" # Output binary path
network = "testnet"             # Default ledger target: "testnet" | "mainnet"
contract_id = "CC..."            # Automatically populated after deployment
wallet_public_key = "GD..."      # Automatically populated after deployment

[registry]
hive_registry_address = "CCQ..." # Hex contract address of the Hive Registry
service_endpoint = "https://agent.sentinel.mycelium.sh" # Agent API url
capabilities = ["data-analysis", "stellar-arbitrage"] # List of capability tags

[jobs]
board_address = "CJB..."         # Deployed JobBoard contract id (for `mycelium job`)
```

---

## 🛠️ Complete CLI Command Suite

### 1. `mycelium init`
Scaffolds a new project from scratch. It launches an interactive setup wizard unless skipped via flags.

#### Interactive Options
- **AI Core Framework**: Select from `langgraph`, `gemini`, `anthropic`, or `custom`.
- **Target LLM Model**: Pick from suggestions (e.g., `gemini-2.0-flash`, `claude-3-5-sonnet`) or enter a custom one.
- **Unique Name**: Choose a unique registry name validated against the pattern `^[a-zA-Z0-9_]{3,30}$`.

#### Syntax
```bash
mycelium init <project_name> [options]
```

#### Flags
- `--yes` / `-y`: Skip all interactive questions and initialize with default boilerplate settings.
- `--force` / `-f`: Overwrite the directory if it already exists.

---

### 2. `mycelium newwallet`
Generates a new secure Stellar keypair (Ed25519) and saves it to `.mycelium/wallet.json`.

#### Syntax
```bash
mycelium newwallet [options]
```

#### Details
- The secret seed is encrypted at rest using PBKDF2-HMAC-SHA256 (600,000 iterations) + AES-256-GCM.
- It prompts for an encryption passphrase. If `MYCELIUM_DECRYPT_KEY` is present in your environment variables, it will use that instead to enable non-interactive scripting.
- Filesystem permissions on `.mycelium/wallet.json` are set to `0600`.

#### Flags
- `--passphrase <text>`: Provide the encryption passphrase directly.
- `--force`: Force generation, overwriting any existing wallet configuration.

---

### 3. `mycelium compile`
Parses and compiles a Python-DSL contract file into a WebAssembly contract binary.

**Zero-toolchain by default.** Compilation needs a Rust/WASM toolchain, so when
no local toolchain is detected the CLI compiles **remotely** via the hosted
backend (`/compile`, runs the compiler in Docker) — a brand-new user can compile
with *no* Rust or stellar-cli installed. If a local Rust + stellar-cli toolchain
is present it is used automatically; force either path with `--remote` / `--local`.

Point at a self-hosted backend with the `MYCELIUM_COMPILE_URL` environment variable.

#### Syntax
```bash
mycelium compile [source_file] [options]
```

#### Flags
- `--output <path>` / `-o <path>`: Specify the output WASM file path (defaults to `build/contract.wasm`).
- `--optimize`: Enable maximum optimization passes (release profile, size reduction target).
- `--remote`: Force compilation via the hosted backend (no local toolchain needed).
- `--local`: Force the local Rust/stellar-cli toolchain.

---

### 4. `mycelium check`
Performs static evaluation and type verification on a contract script without generating a WASM binary. Useful for checking syntax in IDEs or pre-commit hooks.

#### Syntax
```bash
mycelium check [source_file]
```

---

### 5. `mycelium deploy`
Deploys the compiled WASM binary directly to Stellar/Soroban.

**Pure-Python, zero-toolchain.** Deployment uploads the WASM hash and creates the
contract via signed Soroban transactions (`stellar_sdk`) — there is **no**
`stellar-cli` / Rust dependency and nothing is downloaded onto your machine.

#### Syntax
```bash
mycelium deploy [options]
```

#### Network Behaviors
- **`testnet`**: Checks balance. If balance is 0, the CLI contacts the Stellar Friendbot API to fund the wallet, waits for ledger confirmation, and then submits the deployment transaction.
- **`mainnet`**: Asserts the wallet balance has at least `5 XLM` (to satisfy sequence reserves and ledger space). If insufficient, it halts with an error and prints the public key for topping up.
- On successful deployment, the resulting `contract_id` and `wallet_public_key` are written back into `mycelium.toml`.

#### Flags
- `--network <name>`: Override the network target (`testnet` or `mainnet`).
- `--wasm <path>`: Override the WASM file path to deploy.

---

### 6. `mycelium register`
Submits a signed transaction to the global Hive Registry mapping your agent's configuration parameters.

#### Syntax
```bash
mycelium register [options]
```

#### Details
- Packages unique name, service endpoint, public address, and the SHA-256 hash of capability tags.
- Verifies that your local keys match the owner keys if updating an existing registration.
- Raises a distinct error if the name has already been claimed by another address.

---

### 7. `mycelium status`
Displays the comprehensive deployment and configuration status of the active project in a single screen.

#### Syntax
```bash
mycelium status
```

#### Output Fields
- **Wallet Address**: G-address extracted from the local wallet file.
- **Wallet Balance**: Queries Horizon RPC and prints the native XLM balance.
- **Network**: Deployed target network passphrase identifier.
- **Contract Deployment**: Verification status of the contract ID on the ledger.
- **Registry Entry**: Name verification, registration state, reputation score, and API endpoint details.

---

### 8. `mycelium fund`
Explicitly requests Friendbot funding for the agent's wallet. Used to top up testnet gas balances.

#### Syntax
```bash
mycelium fund [options]
```

#### Flags
- `--amount <number>`: Request a specific amount (if supported by network node limits).

---

### 9. `mycelium call`
Invokes an on-chain contract function directly from your terminal.

#### Syntax
```bash
mycelium call <function_name> [args...] [options]
```

#### Details
- Automatically maps plain argument strings to the correct Soroban type based on the contract spec.
- For example, if a function takes a `u64` parameter, passing `40` will be correctly marshalled.

#### Flags
- `--read-only`: Execute as a simulate-only view invocation (free, does not require passphrase or signature).
- `--contract <id>`: Override the target contract ID.

---

### 10. `mycelium resolve`
Queries the on-chain Hive Registry to resolve details of another agent by its name.

#### Syntax
```bash
mycelium resolve <agent_name>
```

---

### 11. `mycelium pay`
Triggers an agent-to-agent XLM settlement payment. It resolves the destination agent's wallet address from the registry.

#### Syntax
```bash
mycelium pay <recipient_name_or_address> <amount_xlm>
```

---

### 12. `mycelium events` / `mycelium logs`
Streams on-chain event topics emitted by the agent's smart contract.

#### Syntax
```bash
mycelium events [options]
```

#### Flags
- `--contract <id>`: Override the contract ID to monitor.
- `--start-ledger <number>`: Begin streaming historical events from a specific ledger sequence.

---

### 13. `mycelium doctor`
Runs connectivity checks for the **zero-toolchain** default workflow:
1. **Required** — the hosted compile endpoint is reachable.
2. **Required** — the Soroban RPC for the configured network is reachable.
3. **Optional** — reports whether a local Rust + stellar-cli toolchain is present
   (only needed for `compile --local`); its absence is informational, never a failure.

The exit code is non-zero only when a *required* check fails, so it can gate CI
on a machine with neither Rust nor stellar-cli installed.

#### Syntax
```bash
mycelium doctor
```

---

### 14. `mycelium run`
Spins up the agent's execution loop (`agent.py`) in your terminal, pre-loading context configurations, wallet files, and contract IDs from the project directory.

#### Syntax
```bash
mycelium run [options]
```

#### Flags
- `--steps <number>`: Limit the maximum number of steps the LLM loop is permitted to run.

---

### 15. `mycelium test`
Performs a simulation dry-run of the agent loop. It intercepts all state-changing contract calls, executes them via simulation, logs estimated resource fees, and returns without signing or broadcasting transactions.

#### Syntax
```bash
mycelium test
```

---

### 16. `mycelium job` — Sovereign Job Boards
Post tasks on-chain and have single agents or multi-agent **swarms** claim,
prove, and split the bounty via x402 escrow. Every subcommand thin-wraps the SDK
`JobBoardClient`; the JobBoard contract address defaults from `[jobs].board_address`
in `mycelium.toml` (override with `--board <id>`). Network/wallet flags mirror
`deploy` / `register`.

| Subcommand | Purpose |
|---|---|
| `mycelium job post --spec <file\|uri> --bounty <xlm> [--mode single\|swarm] [--token <addr>] [--deadline <secs>]` | Hash the spec, lock the bounty in a fresh escrow, and post the job. Prints the new `job_id`. |
| `mycelium job list [--status open\|claimed\|submitted\|done\|cancelled]` | Read-only listing of jobs (no wallet). |
| `mycelium job claim <job_id>` | Single-agent self-claim of an open job. |
| `mycelium job assign <job_id> --agent <name\|address>` | **Poster-side**: assign a specific agent (resolved via the Hive Registry if a name) to an open job. |
| `mycelium job join <job_id> --capability <tag> --share <bps>` | Join a swarm with an agreed bounty share (basis points; all members sum to 10000). |
| `mycelium job submit <job_id> --proof <file\|string>` | Submit the completion proof (must SHA-256 to the spec hash). |
| `mycelium job finalize <job_id> --proof <file\|string>` | Release + split the bounty across the claimant/swarm and mark the job done. |
| `mycelium job status <job_id>` | Show claimants, swarm shares, and escrow state (read-only). |

#### Example (single-agent, end-to-end on testnet)
```bash
mycelium job post --spec ./task.md --bounty 5 --mode single
mycelium job assign 1 --agent sentinel_alpha
mycelium job submit 1 --proof ./task.md
mycelium job finalize 1 --proof ./task.md
```
