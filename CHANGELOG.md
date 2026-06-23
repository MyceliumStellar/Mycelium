# Changelog

All notable changes to the Mycelium framework (SDK, CLI, compiler, and Web IDE)
are documented here. The four components are versioned together.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
