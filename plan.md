# Mycelium: CLI-free Toolchain, In-IDE Agent Creation & Sovereign Job Boards

## Context

Three connected goals:

1. **Remove the hard `stellar-cli` dependency.** Today the CLI, SDK, and IDE backend all shell out to the pinned `stellar` binary (auto-downloaded via `ensure_stellar_cli()`) for two operations: `contract build` (compile → WASM, also needs a Rust toolchain) and `contract deploy`. A new user can't deploy/compile without that toolchain landing on their machine. We want the CLI + SDK to work end-to-end with **zero local stellar-cli / Rust install**.

2. **In-IDE agent creation.** The `/agent` route's "Add" button currently only *resolves* existing on-chain agents. We want it to *create* one: a wizard collecting the same inputs as `mycelium init` (project name, provider, API key, model, unique name), scaffolding a new GitHub repo, then opening the playground in an **"agent creation mode"** where the user writes code, compiles, deploys, and registers — all in-browser.

3. **Roadmap: Sovereign Job Boards.** Post a task on-chain; single agents or multi-agent swarms claim, coordinate, execute, and split bounties via x402 payments. Full contract + SDK + CLI + UI design with phased milestones.

Key facts from exploration:
- Everything except `build` and `deploy` is **already pure-Python** via `stellar_sdk` (`sdk/mycelium_sdk/context.py`).
- The **frontend playground already deploys with pure-JS stellar-sdk** (`uploadContractWasm` + `createCustomContract` + Freighter) — proving deploy needs no binary. We mirror that in Python.
- The IDE backend already has a hosted `/compile` (Docker/`mycelium-compiler:latest`) at `ide/backend/main.py:362`.
- ⚠️ The frontend is a **non-standard Next.js** — per `ide/frontend/AGENTS.md`, read `node_modules/next/dist/docs/` before writing any frontend code.

---

## Part 1 — Remove the local stellar-cli requirement

### 1a. Pure-Python deploy (eliminates the binary for deploy/register/escrow)

Replace every `stellar contract deploy` subprocess with a `stellar_sdk` transaction that does **upload WASM hash** then **create contract**, signed with the wallet keypair already loaded in `AgentContext`.

**New helper** — add `deploy_contract()` to `sdk/mycelium_sdk/context.py` as an `AgentContext` method (reuses `self.soroban_rpc`, `self.keypair`, `self.network_passphrase`, and the existing retry/poll helpers in `mycelium_sdk/rpc.py`):
- Build tx with `TransactionBuilder(...).append_upload_contract_wasm_op(contract=wasm_bytes)`, `prepare_transaction`, sign, submit, poll → extract WASM hash from the settled meta (reuse the `_decode_tx_result` pattern at `context.py:399`).
- Build second tx with `.append_create_contract_op(wasm_id=wasm_hash, address=self.keypair.public_key)`, prepare/sign/submit/poll → derive the contract id.
- Mirror the polling/`with_retry` logic already in `call_contract` (`context.py:315-336`).

This is the single source of truth; the three call sites below delegate to it.

**Refactor call sites to drop `ensure_stellar_cli()` + `subprocess`:**
- `cli/mycelium_cli/commands/deploy.py:116-129` — replace the subprocess block with `AgentContext(wallet_path, network, passphrase).deploy_contract(wasm_bytes)`. Keep the existing balance-check / Friendbot funding (`deploy.py:103-114`) and the `mycelium.toml` write-back (`deploy.py:132-138`).
- `sdk/mycelium_sdk/x402/settlement.py:97-118` (`_deploy_escrow_instance`) — replace with `self.context.deploy_contract(escrow_wasm_bytes)`.
- `ide/backend/main.py:382-469` (`/api/deploy`) — replace subprocess with the Python deploy path (load `stellar_sdk` directly; keep the generate-and-Friendbot-fund branch at `main.py:408-427`).

### 1b. Hosted compile (eliminates Rust + stellar-cli for `build`)

`contract build` genuinely needs the Rust/WASM toolchain, so move it server-side instead of onto the user's machine.

- **CLI**: add a `--remote` path (and make it the default when no local toolchain is detected) to `cli/mycelium_cli/commands/compile.py`. It POSTs `{filename, source_code}` to the IDE backend `/compile` endpoint (`ide/backend/main.py:362`) and writes the returned base64 WASM to `build/contract.wasm`. Local compile stays available via an explicit `--local` flag for users who *do* have the toolchain.
- Add a config/env knob (e.g. `MYCELIUM_COMPILE_URL`, default to the hosted backend) so self-hosters can point elsewhere.
- The hosted `/compile` already runs the compiler in Docker (`mycelium-compiler:latest`) which bundles stellar-cli — no change needed there beyond confirming it's reachable.

### 1c. Doctor + docs

- `cli/mycelium_cli/commands/doctor.py` — demote the stellar-cli / Rust / wasm-target checks (`_check_stellar_cli`, etc.) from hard failures to **optional "local compile" capability** notes; add a check that the remote compile + RPC endpoints are reachable. The default happy path must pass with neither Rust nor stellar-cli installed.
- Update `cli.md`, `sdk.md`, `README.md`, `docs/compiler.md` to describe the zero-toolchain default and the optional local path.

### Verification (Part 1)
- In a clean venv with **no Rust and no stellar binary on PATH**: `mycelium init demo && mycelium newwallet && mycelium compile && mycelium deploy --network testnet && mycelium register`. Expect a real testnet contract id + Hive registration tx, no binary download.
- `pytest cli/tests sdk/tests` — update/extend deploy tests to assert the Python path (mock `SorobanServer`).
- Confirm escrow deploy via an x402 flow (`a2a_demo.py` or an SDK test) still returns a live escrow id.

---

## Part 2 — In-IDE Agent Creation (`/agent` → wizard → playground creation mode)

### 2a. Wizard on the `/agent` "Add" button
`ide/frontend/src/app/agent/page.tsx` — the current search form (`page.tsx:533-575`) keeps its resolve behavior, but add a distinct **"+ Create Agent"** button opening a multi-step modal that mirrors `mycelium init` (`cli/mycelium_cli/commands/init.py`):

1. **Project / unique name** — validate against `^[a-zA-Z0-9_]{3,30}$` (same regex as `init.py:18`).
2. **Provider** — `langgraph | gemini | anthropic | openai | ollama | custom` (`VALID_FRAMEWORKS`).
3. **API key** (for cloud providers) — used for live model discovery.
4. **Model** — fetched from the provider's catalogue. Reuse the discovery logic in `sdk/mycelium_sdk/models.py` via a **new backend proxy endpoint** `POST /api/models` (avoids exposing the API key in the browser / CORS issues), mirroring `init.py:_select_model`.

Requires GitHub OAuth (reuse the playground's existing JWT/login flow); if not logged in, the wizard triggers login first.

### 2b. Backend scaffold endpoint
Add `POST /api/agents/scaffold` to `ide/backend/main.py` (auth-gated via `get_current_user_session`):
- Create the repo by reusing the existing `create_repository` logic (`main.py:243`).
- Commit the scaffolded files using the existing `commit_repo_file` logic (`main.py:322`): `mycelium.toml`, `agent.py`, `contract.py`, `.gitignore`, `README.md` — generated by **reusing the templates in `cli/mycelium_cli/commands/init.py`** (`_build_config`, contract/agent templates). Factor those templates into a shared helper importable by both CLI and backend so they don't drift.
- **API key handling**: do *not* commit `.env` to GitHub. Store the provider key encrypted (reuse `encrypt_token` / Firebase `user_credentials`, `main.py:177`) and inject it at run/deploy time, OR collect it again at deploy time. (Decide during implementation; default = encrypted server-side, never in the repo.)
- Wallet: generate the encrypted wallet via the existing `crypto` helpers; surface the public key. The encrypted `wallet.json` can live in the repo (it's encrypted at rest, matching CLI behavior) — passphrase is never stored.

### 2c. Playground "agent creation mode"
`ide/frontend/src/app/playground/page.tsx` — accept a query param (e.g. `?repo=<name>&mode=create`) that:
- Auto-loads the scaffolded repo's files.
- Surfaces a guided rail/checklist: **Write → Compile → Deploy → Register**, reusing the existing client-side compile (`/compile`) and Freighter deploy pipeline (`playground/page.tsx:1176-1408`).
- After deploy, a **Register** step invokes `register_agent` on the Hive Registry contract (`REGISTRY_ADDRESS` in `agent/page.tsx`) client-side via stellar-sdk + Freighter, writing `unique_name`, capabilities, endpoint, model, role (mirrors `cli/.../register.py` → `HiveClient.register`). On success, route back to `/agent` and the new node appears via existing on-chain discovery.

### Verification (Part 2)
- Logged-in user clicks "Create Agent", completes the wizard → a new private GitHub repo appears with the scaffold; playground opens in creation mode.
- Compile → deploy (Freighter, testnet) → register succeeds; the agent shows up on `/agent` after discovery.
- Confirm no `.env`/plaintext key is committed to the repo.

---

## Part 3 — Sovereign Job Boards (full design + phased build)

### Architecture
```
Poster ──post_job(spec_hash, bounty, mode)──► JobBoard contract ──locks bounty──► Escrow (x402)
                                                     │
        Agents ──claim_job / join_swarm──────────────┤
                                                     │
   Swarm coordinates off-chain (A2A) ─► submit_proof ─► JobBoard verifies ─► split bounty via EscrowPaymentRouter
```

### 3a. On-chain `JobBoard` contract (`job_board_contract.py`, Mycelium DSL)
State + externals (DSL types per `contract.py` template):
- `post_job(spec_uri, spec_hash, bounty_amount, token, mode)` where `mode ∈ {single, swarm}`; locks the bounty into an escrow instance (reuse `escrow_contract.py` / `EscrowPaymentRouter.create_locked_escrow`).
- `claim_job(job_id)` (single, agent self-claims) / `assign_agent(job_id, agent)` (poster designates a specific agent) / `join_swarm(job_id, capability_tag, share_bps)` (swarm) — records claimants and agreed bounty shares (basis points, must sum to 10000).
- `submit_proof(job_id, proof)` — proof must SHA-256 to `spec_hash` (matches escrow `claim_funds` semantics at `escrow_contract.py`).
- `finalize(job_id)` — releases the escrow and **splits the bounty across swarm members per their shares** via x402.
- `cancel_job` / `expire` — refund path after deadline (reuse escrow `refund`).
- Emits `job_posted`, `job_claimed`, `swarm_joined`, `job_completed` events (so the off-chain indexer + `/agent` UI can discover jobs, mirroring existing `agent_registered` discovery).

### 3b. SDK layer (`sdk/mycelium_sdk/jobs.py`)
- `JobBoardClient(context, board_address)` with `post_job`, `claim_job`, `assign_agent`, `join_swarm`, `submit_proof`, `finalize`, `list_open_jobs` (read-only sim via `call_contract(read_only=True)`).
- **Multi-agent split**: extend `EscrowPaymentRouter` to support N-way release (`split_release(escrow_id, [(provider, share_bps)...])`) so bounties divide across a swarm; build on the existing single-provider `release_funds` (`settlement.py:75`).
- Swarm coordination uses the existing A2A primitives (`a2a_demo.py`, `hive_registry.py` discovery) — agents find collaborators by capability tag in the Hive Registry, agree shares off-chain, then `join_swarm`.

### 3c. CLI commands (`cli/mycelium_cli/commands/jobs.py`)
Job Boards must be fully drivable from the CLI, not just the SDK/UI. Add a `mycelium job` command group (Typer sub-app registered in `cli/mycelium_cli/main.py`) that thin-wraps `JobBoardClient`, reusing wallet load + passphrase resolution (`_resolve_passphrase` in `main.py`) exactly like `deploy`/`register`:
- `mycelium job post --spec <file|uri> --bounty <xlm> --mode single|swarm [--token <addr>] [--deadline <secs>]` — hashes the spec, locks the bounty escrow, prints the new `job_id`.
- `mycelium job list [--status open|claimed|done]` — read-only sim listing jobs (via `list_open_jobs`).
- `mycelium job claim <job_id>` — single-agent self-claim.
- `mycelium job assign <job_id> --agent <unique_name|address>` — **poster-side**: assign a specific agent to a job (resolves the agent via Hive Registry, then records the assignment on-chain via `assign_agent`).
- `mycelium job join <job_id> --capability <tag> --share <bps>` — join a swarm with an agreed share.
- `mycelium job submit <job_id> --proof <file>` — submit the completion proof.
- `mycelium job finalize <job_id>` — release + split the bounty.
- `mycelium job status <job_id>` — show claimants, swarm shares, escrow state.
- Network/wallet flags mirror `deploy`/`register`; board address defaults from `mycelium.toml` (`[jobs].board_address`) with a flag override.

### 3d. UI
- New `/jobs` route (frontend): list open jobs (from indexer/events), "Post a Job" form (spec, bounty, single/swarm), and a job detail view showing claimants, swarm shares, and status.
- Surface "available jobs" to agents on `/agent`.

### 3e. Phased milestones
1. **M1 — Escrow groundwork**: finish `escrow_contract.py` + `EscrowPaymentRouter` single-provider path (already partially present; ROADMAP §6.1). Add N-way `split_release`.
2. **M2 — JobBoard contract + CLI**: author + compile `job_board_contract.py` (incl. `assign_agent`); ship the `mycelium job` command group (3c); single-agent post→assign/claim→proof→finalize end-to-end on testnet via CLI.
3. **M3 — Swarm mode**: `join_swarm` + share accounting + multi-way bounty split; A2A coordination demo extending `a2a_demo.py`.
4. **M4 — Indexer + UI**: off-chain event indexer for jobs (ties into ROADMAP §5 "Off-Chain Indexer"); `/jobs` route + agent-facing job feed.

### Verification (Part 3)
- Unit tests for `JobBoardClient` and `split_release` (mock RPC).
- CLI E2E (testnet): `mycelium job post` → `mycelium job assign --agent <name>` → `mycelium job submit --proof` → `mycelium job finalize`, asserting the bounty lands in the assigned agent's wallet.
- Swarm E2E: post a swarm job, two test agents `join_swarm` with 60/40 shares, submit valid proof, `finalize` → confirm both wallets receive the correct split.

---

## ROADMAP.md updates
Add to `ROADMAP.md`:
- New **"Zero-Toolchain Operation"** entry under shipped/§2 (pure-Python deploy + hosted compile).
- New **"In-IDE Agent Creation"** entry under §2 Web IDE extensions.
- New top-level **"💼 Sovereign Job Boards"** section with the M1–M4 phasing above, cross-referencing §6.1 (escrow) and §5 (indexer).

---

## Suggested implementation order
1. Part 1a (pure-Python deploy) — unblocks everything, smallest blast radius.
2. Part 1b/1c (hosted compile + doctor/docs).
3. Part 2 (agent creation wizard) — depends on 1 being solid for in-browser deploy/register UX.
4. Part 3 (Job Boards) — M1→M4, largest, builds on the escrow + registry foundations.

## Notes / caveats
- Frontend: **read `ide/frontend/node_modules/next/dist/docs/` before writing any Next.js code** (`AGENTS.md` warns the framework diverges from standard Next.js).
- Verify `stellar_sdk` version in use exposes `append_upload_contract_wasm_op` / `append_create_contract_op` (the pinned `stellar-sdk>=12,<13` per `context.py:71` does); adjust op names if the API differs.
- Keep `ensure_stellar_cli()` available for opt-in local compile, but it must no longer be on any default path.
