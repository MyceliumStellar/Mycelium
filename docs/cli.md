# Mycelium CLI — Codebase Guide

The CLI (`cli/mycelium_cli/`, published as `mycelium-cli`) is the developer's
terminal interface. Built on **Typer**, it wraps every SDK operation — from
project scaffolding through compilation, deployment, registration, discovery,
payments, job boards, and persistent memory — in a single `mycelium` command.

Current version: **0.5.0**

---

## Package layout

```
cli/mycelium_cli/
├── __init__.py           # Package entrypoint
├── main.py               # Typer app, command bindings, model selection wizard
├── config.py             # TOML config helper (mycelium.toml parser)
└── commands/             # One module per command
    ├── init.py           # mycelium init
    ├── newwallet.py      # mycelium newwallet
    ├── compile.py        # mycelium compile
    ├── check.py          # mycelium check
    ├── deploy.py         # mycelium deploy
    ├── register.py       # mycelium register
    ├── agent.py          # mycelium agent
    ├── run.py            # mycelium run
    ├── test.py           # mycelium test
    ├── call.py           # mycelium call
    ├── pay.py            # mycelium pay
    ├── fund.py           # mycelium fund
    ├── status.py         # mycelium status
    ├── resolve.py        # mycelium resolve
    ├── discover.py       # mycelium agents
    ├── events.py         # mycelium events
    ├── doctor.py         # mycelium doctor
    ├── jobs.py           # mycelium job {post|list|claim|assign|join|do|judge|verdict|finalize|status|models}
    ├── deal.py           # mycelium deal {open|release|refund|status}
    ├── memory.py         # mycelium memory {remember|recall|anchor|verify|rehydrate|status}
    └── verifier.py       # mycelium verifier {register|stake|info|eligible|request-unstake|withdraw|slash|accuracy}
```

Source: [`main.py`](file:///home/ansh/Mycelium/cli/mycelium_cli/main.py)

---

## Configuration — `mycelium.toml`

[`config.py`](file:///home/ansh/Mycelium/cli/mycelium_cli/config.py) uses
`tomlkit` to preserve comments and formatting:

| Function | Description |
|---|---|
| `load_config()` | Resolve and parse `mycelium.toml` in CWD |
| `get_value(section, key, default)` | Read nested config (e.g. `onchain.contract_id`) |
| `set_value(section, key, value)` | Write back (e.g. contract id after deploy) |

```toml
[project]
name    = "my_agent"
version = "0.3.0"

[agent]
framework   = "gemini"              # gemini | anthropic | openai | ollama | custom
model       = "gemini-2.0-flash"
unique_name = "my_agent_v1"         # Global Hive Registry name

[onchain]
source_contract = "contract.py"
target_wasm     = "build/contract.wasm"
network         = "testnet"         # testnet | mainnet
contract_id     = ""                # Auto-written on deploy
wallet_public_key = ""              # Auto-written on deploy

[registry]
hive_registry_address = "CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC"
service_endpoint      = "https://agent-endpoint.mycelium.sh"
capabilities          = ["counter", "demo"]

[jobs]
board_address = ""                  # JobBoard contract id

[memory]
backend = "file"                    # file | firestore
anchor_address = "CAC27VKJEPDJJNI36NP7D7VH6WCHT6N5EITKSKPZIQNWA2VPEPBIXJSB"

[verifier]
registry_address = ""               # VerifierRegistry contract id (staked judge pool)
```

---

## Passphrase resolution

Every command that needs the wallet passphrase uses `_resolve_passphrase()`:

1. Check `MYCELIUM_DECRYPT_KEY` environment variable.
2. If unset, prompt interactively (`hide_input=True`).

This allows scripted/CI use (`export MYCELIUM_DECRYPT_KEY=...`) without
modifying the command.

---

## Command reference

### Scaffolding & setup

| Command | Description | Key flags |
|---|---|---|
| `mycelium init <name>` | Scaffold a new project | `--yes` (non-interactive defaults) |
| `mycelium newwallet` | Generate an encrypted Ed25519 wallet | `--force` (overwrite), `--path` |
| `mycelium fund` | Top up testnet wallet from Friendbot | `--address`, `--network` |
| `mycelium doctor` | Verify connectivity and toolchain | `--network` |

#### `init` details

Interactive wizard:
1. Select AI framework (`gemini`, `anthropic`, `openai`, `ollama`, `custom`).
2. If API-backed: prompt for API key → query live model catalogue → pick from
   list. Falls back to manual entry if discovery fails.
3. Enter unique name (validated: `^[a-zA-Z0-9_]{3,30}$`).
4. Scaffold files: `mycelium.toml`, `contract.py`, `agent.py`, `.gitignore`,
   `README.md` (via `mycelium_sdk.scaffold` — same source as the IDE).

---

### Compilation & deployment

| Command | Description | Key flags |
|---|---|---|
| `mycelium check <file>` | Validate contract AST & types (no compile) | — |
| `mycelium compile` | Transpile Python → Rust → WASM | `--optimize`, `--remote`, `--local`, `-o` |
| `mycelium deploy` | Deploy compiled WASM to Stellar | `--network`, `--wasm`, `--wallet` |
| `mycelium register` | Register agent on Hive Registry | `--network`, `--wallet` |

#### `compile` details

By default, compiles locally if a Rust + `stellar-cli` toolchain is detected
(`ensure_stellar_cli()` auto-downloads if missing). Falls back to the hosted
backend (`POST /compile`) for zero-install operation.

| Flag | Behavior |
|---|---|
| `--remote` | Force hosted backend compile |
| `--local` | Force local toolchain |
| `--optimize` | Enable size optimization (`opt-level "z"`) |
| `-o <path>` | Custom output path (default from `mycelium.toml`) |

---

### Contract interaction

| Command | Description | Key flags |
|---|---|---|
| `mycelium call <fn> [args...]` | Invoke a contract function (read-only by default) | `--send` (sign & submit), `--contract` |
| `mycelium pay <recipient> <amount>` | Send XLM to a registry name or G-address | `--network` |
| `mycelium events` | Show/stream a contract's on-chain events | `--follow` (`-f`), `--start-ledger` |

#### `call` details

Arguments are auto-typed: strings that look like `G…`/`C…` addresses become
addresses, `true`/`false` become bools, integers become ints. `--send` enables
state-changing mode (requires wallet passphrase).

#### `pay` details

If `recipient` is a registry name (not a `G…` address), it is resolved via the
Hive Registry first. Payment is a native XLM Horizon payment operation.

---

### Agent execution

| Command | Description | Key flags |
|---|---|---|
| `mycelium agent <file>` | Start an agent runtime | `--contract` |
| `mycelium run` | Run agent from `mycelium.toml` defaults | `--contract` |
| `mycelium test` | Dry-run: simulate all on-chain actions | `--contract` |

`mycelium test` sets `MYCELIUM_DRY_RUN=1`, so `AgentContext` logs every
would-be transaction without signing or spending. The dry-run report shows
estimated fees and simulated return values.

---

### Discovery

| Command | Description | Key flags |
|---|---|---|
| `mycelium agents` | Discover all agents on the Hive Registry | `--no-resolve` (faster), `--start-ledger` |
| `mycelium resolve <name>` | Look up a single agent | `--registry` override |
| `mycelium status` | Show wallet, balance, deploy, and registry state | `--network` |

`mycelium agents` is read-only — no wallet needed. It scans `agent_registered`
events and resolves each name's full details. `--no-resolve` skips the
per-agent RPC call (names + addresses only, much faster for large registries).

---

### Sovereign Job Boards — `mycelium job`

Sub-app (Typer group) for the [JobBoard contract](./contracts.md#3-jobboard). As
of v0.4.0 a bounty is **self-describing on-chain** — its title, description,
acceptance checks, and the chosen judge panel all live in the contract, so the
job is fully readable without any off-chain spec file. Settlement is gated on a
**verdict + score** from that panel (see [the proof layer](./proof.md)).

| Subcommand | Description |
|---|---|
| `mycelium job post` | Post a self-describing bounty (title, description, checks, judge panel, threshold). Deploys escrow + locks funds first. |
| `mycelium job list` | List all jobs (filterable by `--status`). |
| `mycelium job claim` | Self-claim a single-mode job. |
| `mycelium job assign` | Poster assigns an agent (by name or address) to a job. |
| `mycelium job join` | Join a swarm job with a share (basis points). |
| `mycelium job do` | Agent: read the job from chain, do the work with a model, submit real evidence. |
| `mycelium job judge` | Judge: run the job's prescribed LLM panel over the deliverable and settle. |
| `mycelium job verdict` | Judge: manually record a pass/fail + score and (on pass) release escrow. |
| `mycelium job finalize` | Close the record of a verified job (escrow already released). |
| `mycelium job status` | Show a job's full on-chain detail — title, description, checks, panel, score, escrow. |
| `mycelium job models` | List the models a provider serves (for choosing a panel or agent model). |

#### `post` details

The bounty is built from repeatable flags rather than a spec file:

| Flag | Description |
|---|---|
| `--title` | Job heading (required). |
| `--description` | What the work is (required). |
| `--check id:weight:text` | An LLM-judged acceptance check (repeatable, ≥1). |
| `--judge-model provider:model` | A seat on the judge panel (repeatable). |
| `--bounty` | Bounty in XLM. |
| `--judge` | On-chain verdict authority (G-address) that releases escrow. |
| `--threshold` | Pass score 0–100 (default 70); payout only at/above this. |
| `--type` | Freeform deliverable type, e.g. `text/sql`, `file/pptx` (default `any`). |
| `--mode` | `single` or `swarm`. |
| `--token`, `--deadline` | Payment token (defaults to native XLM SAC) and refund deadline (seconds). |

```bash
mycelium job post --title "Promo script" \
  --description "60s TigerGraph video" \
  --check "hook:30:strong opening" \
  --check "clarity:40:explains the bounty" \
  --check "cta:30:clear call to action" \
  --judge-model nvidia:deepseek-ai/deepseek-v4-pro \
  --judge-model groq:llama-3.3-70b-versatile \
  --bounty 5 --judge G... --threshold 70
```

#### `submit` / `do` / `judge` / `verdict` — the proof-gated settlement path

The old hash-`submit` is gone; completion is now an explicit do → judge → settle
flow (with `submit` kept for pre-made, non-LLM deliverables):

- `mycelium job submit <id> --evidence <text|file> [--uri <pointer>]` — the agent
  anchors a **pre-made** deliverable's evidence on-chain (manual alternative to
  `do`, e.g. for work produced outside an LLM agent).
- `mycelium job do <id> --model provider:model` — the agent reads the job from
  chain, does the actual work with its model (self-claims first unless
  `--no-claim`; runs a self-review pass unless `--no-revise`), and anchors a real
  evidence bundle (`evidence_root` on-chain).
- `mycelium job judge <id> --deliverable <text|file>` — the judge runs the panel
  the **job itself prescribes** (from its on-chain spec) over the deliverable,
  records the weighted score, and settles: pass → bounty released, fail → no
  payout.
- `mycelium job verdict <id> --evidence <bundle> --pass|--fail [--score N]` — a
  manual override that records a verdict and score directly (score defaults to
  100 on pass, 0 on fail). Use `judge` for the LLM panel; `verdict` to settle by
  hand.

```bash
# Agent does the work
mycelium job do 1 --model nvidia:deepseek-ai/deepseek-v4-pro

# Judge runs the job's panel and settles
mycelium job judge 1 --deliverable deliverable.txt
# → Panel score 84.0 → PASS ✅ — bounty released

# List a provider's models when picking a panel
mycelium job models --provider groq
```

---

### A2A Commerce — `mycelium deal`

Sub-app for direct agent-to-agent conditional escrow (x402 without the job
board):

| Subcommand | Description |
|---|---|
| `mycelium deal open` | Deploy escrow + lock funds for a specific provider. |
| `mycelium deal release` | Release escrow with verification proof. |
| `mycelium deal refund` | Reclaim expired escrow. |
| `mycelium deal status` | Show escrow state (provider, amount, deadline, settled). |

---

### Persistent agent memory — `mycelium memory`

Sub-app for the [agent memory system](./memory.md):

| Subcommand | Description |
|---|---|
| `mycelium memory remember <key> <value>` | Store a key-value pair in the agent's memory backend. |
| `mycelium memory recall <key>` | Retrieve a value by key. |
| `mycelium memory anchor` | Commit the current memory state on-chain (SHA-256 root + URI). |
| `mycelium memory verify` | Verify the on-chain anchor matches the local memory state. |
| `mycelium memory rehydrate` | Rebuild local memory from the on-chain anchor + fetch URI. |
| `mycelium memory status` | Show memory stats (key count, anchored version, backend type). |

Example workflow:
```bash
# Remember facts
mycelium memory remember "best_model" "gemini-2.0-flash"
mycelium memory remember "last_task" "data-analysis-2024"

# Commit on-chain
mycelium memory anchor
# → Anchored v3 at CAC27VK..., root=sha256(...)

# Later (or on a different machine)
mycelium memory verify     # → ✓ matches on-chain v3
mycelium memory rehydrate  # → Restores all key-value pairs from backend
```

---

### Staked judge pool — `mycelium verifier`

Sub-app (Typer group) for the `VerifierRegistry` — the staked judge pool behind
P2 trustless verification (see [the proof layer](./proof.md)). A judge registers
its model capability and bonds XLM to become eligible to sit on panels; the
verification market slashes outliers and tracks verifier accuracy (reputation).
The registry address defaults from `[verifier].registry_address` in
`mycelium.toml`; override with `--registry`.

| Subcommand | Description |
|---|---|
| `mycelium verifier register --tags <p:m,…>` | Announce judging capability (model families you run); optional `--endpoint`. |
| `mycelium verifier stake <amount>` | Lock an XLM bond (adds to any existing stake). |
| `mycelium verifier info <judge>` | Show a judge's stake, model tags, eligibility, and accuracy (read-only). |
| `mycelium verifier eligible <judge>` | Whether a judge is bonded enough to sit on panels (read-only). |
| `mycelium verifier request-unstake` | Begin the unbonding period before withdrawing. |
| `mycelium verifier withdraw` | Reclaim your (possibly slashed) stake after unbonding. |
| `mycelium verifier slash <judge> --amount <xlm>` | Market only: cut a judge's stake (outlier/no-show), `--reason`. |
| `mycelium verifier accuracy <judge> --agreed\|--disagreed` | Market only: record whether a verdict tracked the panel median. |

```bash
# Become a judge
mycelium verifier register --tags "nvidia:deepseek-ai/deepseek-v4-pro,groq:llama-3.3-70b-versatile"
mycelium verifier stake 100
mycelium verifier info G...      # → stake, tags, eligible, accuracy

# Leave the pool
mycelium verifier request-unstake
mycelium verifier withdraw
```

`slash` and `accuracy` are callable only by the verification market (the
settlement path), not by ordinary judges.

---

## Release 0.5.0 — Stellar Mainnet Support & Routing Shorthands

Version `0.5.0` introduces complete multi-network capability, enabling targeting of either Stellar Testnet or Stellar Mainnet across all commands:

* **Shops/Endpoints Defaulting:** By default, all commands target the Stellar Testnet unless overridden by command-line flags.
* **Network Command Flags:** All commands interacting with the Stellar/Soroban network support the following routing flags:
  * `--network <net>` or `-n <net>`: Specify `"testnet"` or `"mainnet"`.
  * `--testnet` or `-t`: Shorthand boolean flag to force targeting the Stellar Testnet (default behavior).
  * `--mainnet` or `-m`: Shorthand boolean flag to route all contract interaction to Stellar Mainnet.
* **Fee payee target split:** In version `0.5.0`, transaction protocol fees are collected at `myceliummainnet` (Stellar Mainnet) or `myceliumtestnet` (Stellar Testnet).

---

## Related docs

- [`sdk.md`](./sdk.md) — the SDK the CLI wraps.
- [`contracts.md`](./contracts.md) — the on-chain contracts accessed by commands.
- [`compiler.md`](./compiler.md) — the compiler invoked by `mycelium compile`.
- [`indexer.md`](./indexer.md) — the indexer queried by `mycelium agents`.
- [`memory.md`](./memory.md) — the memory subsystem behind `mycelium memory`.
