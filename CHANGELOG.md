# Changelog

All notable changes to the Mycelium framework (SDK, CLI, compiler, and Web IDE)
are documented here. The four components are versioned together.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.2] — 2026-07-02

### CLI & SDK
- **Dynamic CLI ASCII Banner Scaling**: Configured the CLI startup banner to check the terminal window width and automatically scale between the original 67-column block logo, a new 50-column compact ASCII logo, or a text-based fallback to prevent mangling or line-wrapping on narrow panels.
- **Windows Console Width Detection**: Implemented Win32 console API calls via `ctypes` on Windows platforms to detect the actual visible conhost window size rather than the scrollable screen buffer size.
- **Windows conhost ANSI Processing**: Automatically enables and verifies Windows Virtual Terminal Processing to prevent printing raw escape codes (like `←[92m` and `←[0m`) on Windows command prompts.
- **Table Column Truncation & Formatting**: Upgraded `mycelium agents` to use a `rich.table.Table` with auto-wrapping, and conditionally truncates 56-character Ed25519 addresses to 19 characters (`GCBFVJZF...5OOLTZHQ`) when the terminal window is narrower than 120 columns.
- **Rich Dashboard Diagnostics**: Upgraded the `mycelium status` and `mycelium doctor` commands to render diagnostics in styled Rich Panels and Tables for modern, color-coded console output.

## [0.4.1] — 2026-07-02

### CLI & SDK
- **Windows OS Encoding Compatibility:** Configured text-mode file read/write operations (like CLI templates, dotenv loading, wallet storage, and AST checking) to explicitly use `encoding="utf-8"`, preventing `UnicodeEncodeError` when files contain emojis or smart quotes on Windows systems.
- **Path Normalization:** Replaced hardcoded Unix temp paths (`/tmp/...`) with `tempfile.gettempdir()` to support multi-OS execution.
- **Docker Mount Paths:** Normalizes sandbox volume mount paths on Windows hosts by replacing backslashes with forward slashes.
- **Stellar CLI Windows ARM64 Support:** Configured the Stellar CLI bootstrapper to dynamically resolve and retrieve the `x86_64` build on all Windows platforms (including ARM64 snapped machines) so built-in emulation can run the binary.
- **Platform-Specific Doctor Diagnostics:** Updated the doctor command to output Windows-specific Rust toolchain download recommendations.
- **Log Correction:** Fixed bad-sequence retry loop count logs to report `8` attempts instead of `5`.

## [0.4.0] — 2026-06-30

The **proof-of-work** release. Mycelium gains a real **verifiable-work layer**: a
bounty is no longer released by a meaningless hash, but by a **panel of
independent LLM judges** scoring the actual deliverable against the poster's
acceptance checks. This is the agent-to-agent (A2A) trust primitive Stellar
lacks. The compiler rejoins the unified version line; `mycelium-sdk`,
`mycelium-cli`, `mycelium-compiler`, and the `mycelium-stellar` metapackage all
move to `0.4.0`.

> **The tautology that's gone.** Before, `submit_proof`/`claim_funds` checked
> `SHA256(proof) == spec_hash` — satisfied only by echoing the job spec back. It
> proved a worker could *read*, never that work was *done* or *good*. "I need a
> Canva deck" has no preimage. v0.4.0 replaces it with verdict-gated release.

### The proof layer (new)
- **Self-describing on-chain jobs.** A job now carries its `title`, `description`,
  the **acceptance checks** (weighted), and the **poster-chosen judge panel**
  (`provider:model` list) — all stored on-chain and hashed for integrity, so any
  bounty is fully readable straight from the JobBoard contract via `get_job` with
  no off-chain dependency.
- **Multi-LLM judge panel.** The job's chosen panel (heterogeneous models across
  **NVIDIA** and **Groq**) each scores the real deliverable independently; the
  contract settles on the **per-criterion median** against the pass threshold.
  Model diversity defeats single-model prompt-injection; the median defeats a
  single rogue seat. The numeric verdict **score** is written on-chain.
- **Real evidence, not a hash.** The worker submits an `EvidenceBundle` (the
  actual artifacts + per-check claims + provenance); its `evidence_root` **and** an
  `evidence_uri` pointer go on-chain (bulk data stays off-chain by hash — the
  correct split), so the proof is discoverable and tamper-evident.
- **Worker agents.** `ContentAgent` reads a job's rubric from chain, produces the
  deliverable for *any* job type (content, SQL, outlines, …) with a chosen model,
  drafts→self-reviews→revises, and submits real evidence.
- **Single + swarm payout.** A single agent is paid the full bounty on a passing
  verdict; a **swarm** of agents is paid a balanced split per their recorded
  shares — both gated on the same panel verdict.
- **P2 trustless foundation — `VerifierRegistry`.** A staked judge pool: judges
  `register` model capability, `stake` an XLM bond to become eligible, and are
  `slash`ed by the verification market for outlier/no-show verdicts; per-judge
  **accuracy** (verifier reputation) is tracked. (Commit-reveal market, random
  panel selection, and dispute escalation are specified in `PROOF_SYSTEM.md §11`
  as the next stages.)
- **Agent reputation — `ReputationRegistry`.** Portable, on-chain worker
  reputation aggregated from verdict scores (`jobs_done`, `jobs_passed`,
  `avg_score`, `pass_rate`), idempotent per job, credited at verdict time
  (single agent or each swarm member). The A2A trust signal. Plan: §12.

### Added
- **`mycelium_sdk.proof`** package: `Rubric`/`Criterion` (v2: title, description,
  checks, judge panel), `EvidenceBundle`, `Verdict`, `Judge`, `JudgePanel`/`Seat`,
  `ContentAgent`, `VerifierRegistryClient`, `ReputationClient`, and a provider
  registry (`resolve_completer("provider:model")`, `list_models`) for NVIDIA NIM +
  Groq (any OpenAI-compatible endpoint; keys from env).
- **`JobBoardClient`** high-level flow: `post_bounty`, `execute_job`,
  `judge_and_settle` (runs the panel the job prescribes → records verdict+score →
  releases), `fetch_rubric`, plus `submit_evidence`, `record_verdict`,
  `release_bounty`, `settle`.
- **New contracts**: `verifier_registry.py`, `reputation_registry.py`.
- **CLI**: `mycelium job post --title --description --check id:weight:text
  --judge-model provider:model --threshold`, `job do --model`, `job judge`,
  `job models --provider`, richer `job status` (on-chain title/checks/panel/score);
  new **`mycelium verifier`** group (`register|stake|info|eligible|slash|accuracy|
  request-unstake|withdraw`).
- **Indexer** serves on-chain `title`/`description`/`spec` (checks + panel) per
  job, so the bounty page renders the real job; API version `0.4.0`.

### Changed (breaking — contracts redeployed on testnet)
- **Escrow** releases on a **judge's verdict**, not a SHA-256 preimage:
  `initialize(…, judge, …)`; `claim_funds`/`claim_and_split` require
  `judge.require_auth()` and take an `evidence_root` (recorded for audit).
- **JobBoard**: `post_job` now stores `title`/`description`/`spec`/`rubric_hash` +
  `judge`; `submit_proof` → `submit_evidence(evidence_root, evidence_uri)` (no
  hash check); new `record_verdict(passed, score, evidence_root)`; `finalize`
  requires a `verified` job. Lifecycle: open → claimed → submitted →
  verified/rejected → done.
- **SDK reliability** (helps every flow): automatic `txBAD_SEQ` recovery
  (reload account + rebuild/re-sign) and the settle-poll timeout raised 60s → 180s
  for congested testnet.
- **Compiler** rejoins the unified version (`0.2.0` → `0.4.0`). Note for contract
  authors: per-address storage keys use the raw `Address`
  (`storage.set("stake:" + addr, …)`); the compiler maps `"prefix:" + addr` to a
  `(Symbol, Address)` tuple key.

### Verified on testnet
Single-agent (SQL job, NVIDIA+Groq panel → score 98 → paid), 2-agent swarm
(60/40 split), `VerifierRegistry` (stake → slash → ineligible + accuracy),
`ReputationRegistry` (aggregate scores, idempotent). Live addresses are recorded
in `mycelium.toml` (`[jobs]`, `[verifier]`, `[reputation]`). See `PROOF_SYSTEM.md`
for the full architecture and the P2/reputation roadmap.

## [0.3.0] — 2026-06-26

The **scale & hardening** release. Two pre-pitch scaling pillars land — an
**off-chain indexer** that turns agent/job discovery from an O(N) event-scan into
an O(1) hosted lookup, and **persistent agent memory** (a big mutable off-chain
store committed on-chain by a tiny, constant-size anchor). Alongside them, the
money-path and IDE-backend security gaps found in the pre-mainnet audit are
closed. The compiler is unchanged and stays at `0.2.0`; `mycelium-sdk`,
`mycelium-cli`, and the `mycelium-stellar` metapackage move to `0.3.0`.

### Security
- **JobBoard authorization (mainnet blocker).** `submit_proof` now takes a
  `submitter: Address` and calls `submitter.require_auth()`, asserting the
  submitter is the recorded single-mode agent or a swarm member (new
  `ContractError.NOT_CLAIMANT`); `finalize` now calls `poster.require_auth()`.
  Previously either call was unauthenticated, so an unsigned caller could record
  a proof and drive a job to escrow release. Propagated through `JobBoardClient`
  and `mycelium job submit|finalize`; regression tests added.
- **IDE token encryption no longer derives from the JWT key.** Stored secrets
  (GitHub tokens, user API keys) are now encrypted with a key derived via
  HKDF-SHA256 from a **dedicated `TOKEN_ENCRYPTION_KEY`**, independent of
  `JWT_SECRET_KEY`. The old scheme null-padded `JWT_SECRET_KEY` (shared, no
  salt), so a JWT-key leak decrypted every credential. Decryption falls back to
  the legacy key (via `MultiFernet`) so existing credentials keep working and are
  transparently re-encrypted under the new key on next login.
- **IDE compute/money endpoints bounded.** `POST /api/deploy` (can fund + deploy,
  accepts a wallet secret) now requires an authenticated session. `POST /compile`
  stays public — the CLI's zero-install `mycelium compile --remote` depends on it
  — but is no longer an unbounded surface: a 256 KiB source cap plus a rolling
  rate limit (by user id when authenticated, else client IP). CORS tightened from
  `allow_methods=["*"]` to the methods actually served.

### Added
- **Off-chain indexer (`indexer/`, Firestore-backed, hosted).** A verifiable
  cache over full on-chain history (chain stays source-of-truth). `indexer/worker.py`
  is a cursor-tracked, idempotent ingest loop (re-ingest = overwrite, resume from
  the cursor doc with no dupes; `--from-ledger N` backfill); `indexer/api.py`
  serves `GET /agents` (capability + `min_reputation` filters, cursor pagination),
  `/agents/{name}`, `/jobs`, `/jobs/{id}`, `/memory/{owner}`, `/stats`, each
  response carrying `source_contract` + `as_of_ledger` for client-side
  verification. Shared event-scan logic extracted to `mycelium_sdk/events.py`.
  - **SDK/CLI integration:** `HiveClient.discover_agents(prefer_indexer=True)`
    uses the hosted indexer and **falls back to the on-chain event-scan** when it
    is unreachable; `mycelium agents` / `discover` use it when reachable.
  - Distributed as a **hosted service**, not bundled in the pip metapackage;
    sovereign self-hosters can `pip install mycelium-stellar[indexer]`.
- **Persistent agent memory (`mycelium_sdk.memory`, `AgentMemory`).** Big mutable
  private memory stays off-chain; only a tiny `(memory_root, uri, version)`
  commitment goes on-chain via the new **MemoryAnchor** contract — so per-agent
  on-chain footprint is constant regardless of memory size.
  - `remember` / `recall` (off-chain, no tx); `anchor(uri, publish=)` commits the
    content root on-chain; `verify()` recomputes and compares to the anchor;
    `rehydrate()` reads the anchor → fetches the blob → re-hashes → refuses to
    load on mismatch (tamper/rollback protection).
  - **Backends behind one interface:** `LocalVectorBackend` (SQLite + zero-dep
    offline embedder, the OSS default), `SupermemoryBackend` (real
    `api.supermemory.ai` v3 wiring, keyed by the agent's G-address), and
    `TieredBackend` (local cache + cloud at once behind one anchor). All export
    the same canonical blob, so the on-chain root is backend-independent.
  - **Anchoring policy** (`AnchoringPolicy`): anchor at job-completion
    (`on_job_complete`) + a throttled `heartbeat` — never per-write (the cost knob).
  - **CLI:** `mycelium memory remember|recall|anchor|verify|rehydrate|status`.
  - Portability proven on testnet (`memory_demo.py`): write+anchor on machine A →
    rehydrate+verify on machine B → tampered blob rejected.

### Changed
- **Money-path input validation (defense-in-depth; escrow still re-checks
  on-chain).** `create_locked_escrow` rejects non-positive amounts, sub-stroop
  amounts, and amounts above the i128 ceiling **before** deploying anything;
  `post_job` rejects non-positive / sub-stroop bounties; `split_release` rejects
  empty share lists and non-positive basis points; `join_swarm` (SDK + `mycelium
  job join`) validates `0 < share_bps <= 10000` client-side. `list_open_jobs`
  now logs skipped jobs at debug instead of silently swallowing errors.

## [0.2.0] — 2026-06-23

The Sovereign Job Boards release: post tasks on-chain, have single agents or
multi-agent swarms claim them, prove completion, and split bounties via x402
escrow — now working end-to-end on testnet. Adds a `mycelium deal` command group
for direct agent-to-agent (A2A) conditional commerce between two agents.

### Fixed
- **Escrow `initialize` on-chain trap (the bounty-flow blocker).** The bundled
  `escrow.wasm` trapped (`HostError: WasmVm, InvalidAction / UnreachableCodeReached`)
  at the SAC `transfer` cross-contract call during `initialize`, so locking a
  bounty into escrow never succeeded through the SDK path. Root cause was a
  compiler codegen defect (the `env.crypto().sha256(...) != Bytes` comparison
  emitted a bare `.into()` that produced a trapping `Hash<N>` conversion, E0283);
  codegen now emits an explicit `soroban_sdk::Bytes::from(...)`. The escrow WASM
  recompiled from `escrow_contract.py` with `mycelium-compiler:latest` is now
  byte-identical (sha256 `71b3861e…`, 4852 bytes) and verified non-trapping.
  - Validated on Stellar testnet: `create_locked_escrow` → `claim_funds`
    (single payout) and `claim_and_split` (N-way swarm split) all settle.
  - Full Job Boards flow validated end-to-end: `post_job` (locks escrow) →
    `claim_job` / `join_swarm` → `submit_proof` → `finalize` (releases + splits).
    A 60/40 two-agent swarm split landed the exact `+0.4 XLM` on the minority
    recipient with no rounding dust.
- Corrected a stale Stellar SDK install hint in `AgentContext` (`>=12,<13` →
  `>=14,<15`) to match the actual dependency pin.

### Added
- **`mycelium deal` command group — agent-to-agent (A2A) commerce from the CLI.**
  Wires two agents into a *conditional* x402 escrow deal (vs. the unconditional
  `mycelium pay`): the payer locks funds payable to a provider — resolved by Hive
  Registry unique name or raw address — and the provider only collects by
  publishing a proof of the agreed task; the payer reclaims the funds after the
  timeout if undelivered. The escrow enforces the proof (SHA-256) and deadline
  on-chain, so neither side has to trust the other. This is the CLI front door to
  what `a2a_demo.py` / `EscrowPaymentRouter` previously did only in Python.
  - `mycelium deal open --to <name|address> --amount <xlm> --task <file|str>
    [--timeout <secs>] [--token <addr>]` — lock the payment; prints the escrow id.
  - `mycelium deal release <escrow_id> --proof <file|str>` — disburse to the provider.
  - `mycelium deal refund <escrow_id>` — payer reclaims after the deadline.
  - `mycelium deal status <escrow_id>` — read the escrow state (read-only, no wallet).
  - Validated on testnet: agent1 locked 2 XLM to agent2 (resolved by Hive name
    `myc2_dd9246f1`); agent2 released on proof and received the funds.
- `mycelium --version` / `-V` flag and `mycelium_sdk.__version__`.

### Notes
- No schema/API breaking changes versus 0.1.0. The escrow fix ships as a
  recompiled bundled WASM plus the already-landed compiler codegen fix; existing
  0.1.0 deployments of the JobBoard contract are unaffected (the bug was in the
  escrow instance deployed per-job, which is freshly deployed each `post_job`).

## [0.1.0] — Initial release

First public release of the Mycelium framework.

### Compiler
- Python-to-Soroban-WASM compiler (`mycelium_compiler`) for the Mycelium
  contract DSL: `@contract`/`@external`/`@view`, typed-int wrappers
  (`U64`/`U32`/`I128`/…), `Address`, `Bytes`, `Map`, `Vec[...]`, composite
  storage keys, `env.transfer`, `env.crypto().sha256`, events, and `require_auth`.
- Hosted compile in Docker (`mycelium-compiler:latest`, stellar-cli 27.0.0).

### SDK (`mycelium-sdk`)
- `AgentContext`: encrypted wallet load, width-correct SCVal marshalling,
  simulate → sign → submit → poll contract invocation, and read-only sims.
- **Zero-toolchain deploy**: `AgentContext.deploy_contract` — pure-Python signed
  Soroban transactions (upload WASM hash → create contract), no Rust/stellar-cli.
- `HiveClient` agent registration + discovery against the Hive Registry.
- x402 `EscrowPaymentRouter`: `create_locked_escrow`, `release_funds`,
  `split_release` (N-way), `refund`.
- `JobBoardClient` for the Sovereign Job Boards contract: `post_job`,
  `claim_job`, `assign_agent`, `join_swarm`, `submit_proof`, `finalize`,
  `list_open_jobs`.

### CLI (`mycelium-cli`)
- `init`, `newwallet`, `compile` (hosted by default, `--local` opt-in),
  `deploy`, `register`, `discover`/`resolve`, `status`, `fund`, `call`, `pay`,
  `events`, `run`, `test`, `doctor`.
- `mycelium job` command group (`post`, `list`, `claim`, `assign`, `join`,
  `submit`, `finalize`, `status`).

### Web IDE
- Playground with client-side compile (`/compile`) and Freighter deploy.
- In-IDE agent creation wizard (`/agent` → scaffold GitHub repo → playground
  creation mode) and backend `POST /api/agents/scaffold` + `POST /api/models`.
- Pure-Python `POST /api/deploy` (no stellar-cli).

[0.2.0]: #020--2026-06-23
[0.1.0]: #010--initial-release
