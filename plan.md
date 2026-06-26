# Mycelium — Pre-Mainnet Hardening & Build Plan

## Context

Mycelium ships v0.2.0 today: a Python→Soroban compiler, SDK, 18-command CLI, Web IDE, on-chain Hive Registry, x402 escrow, and Sovereign Job Boards — all validated end-to-end on **testnet**. The next step is a **mainnet shift** (after grant funding), and before it everything that assumes testnet, every money-path correctness gap, and every security-sensitive surface must be closed. In parallel, two scaling pillars must be **built before the grant pitch** (per `vision/`): the **off-chain indexer** (makes discovery O(1) — the headline scaling proof) and **persistent agent memory** (off-chain memory + tiny on-chain anchor).

This plan was produced after auditing the live code. The audit confirmed a **mainnet-blocking authorization bug** in the JobBoard contract (`submit_proof` + `finalize` have no `require_auth()`), a **weak shared-key encryption** scheme in the IDE backend (`get_fernet_key` null-pads `JWT_SECRET_KEY`), **unauthenticated** `/api/deploy` and `/compile` endpoints, money-path **validation gaps**, and **testnet hardcodes**. Indexer/memory architecture is designed in `vision/01-offchain-indexer.md` and `vision/02-persistent-agent-memory.md`; this plan turns them into build steps and sequences them with the hardening.

**Decisions locked with the user:**
- Indexer datastore: **Firestore** (not Postgres, not RTDB) — stays in the existing Firebase ecosystem, supports compound queries + `array-contains` for capability tags + real pagination, reuses the Firebase Admin setup already in `ide/backend`. RTDB can't do compound capability+reputation filters server-side.
- Indexer access: **hosted-first** — Mycelium runs one indexer; SDK/CLI/IDE point at its URL over HTTP (mirrors how `mycelium compile` defaults to the hosted `/compile` backend). Self-hosting stays possible for sovereign users. SDK falls back to the on-chain event-scan when the indexer is unreachable.
- Sequencing: indexer + memory + **most hardening land before the grant pitch**; the actual mainnet cutover (network defaults, mainnet smoke tests) follows funding.

**Intended outcome:** a Mycelium that (a) demonstrates million-agent scale via a working hosted indexer + memory anchoring for the pitch, and (b) is safe to point at mainnet — no unauthorized fund release, no weak secrets, no testnet-only assumptions in money paths.

---

## Priority 0 — BLOCKING security fix: JobBoard authorization (do first)

**Problem (confirmed in `job_board_contract.py`):**
- `submit_proof(job_id, proof)` (lines 133-142) — **no `require_auth()`**. Any unsigned caller can record a valid proof on any job.
- `finalize(job_id)` (lines 145-156) — **no `require_auth()`**. Any caller can finalize, and `JobBoardClient.finalize` (`sdk/mycelium_sdk/jobs.py:153`) then calls `EscrowPaymentRouter.split_release` to disburse the bounty.

Combined, an unauthorized actor can drive a job to escrow release. The escrow itself is sound (`escrow_contract.py` gates `claim_funds`/`claim_and_split` on the secret proof preimage), but the JobBoard records the proof on-chain without auth, defeating the secrecy assumption.

**Fix (in `job_board_contract.py`):**
1. `submit_proof` — add `submitter: Address` param, `submitter.require_auth()`, assert submitter is the recorded `agent:` (single) or in `members:` (swarm); else revert (new `ContractError.NOT_CLAIMANT`).
2. `finalize` — load `poster:` from storage and `poster.require_auth()` (mirror `assign_agent` at lines 95-99). The party releasing funds must authorize.
3. Document: proofs must stay secret until `submit_proof`; only the claimant may submit.

**Propagation:**
- `sdk/mycelium_sdk/jobs.py` — pass the new auth address in `submit_proof`/`finalize` (finalize already loads the job → has `poster`/`agent`).
- `cli/mycelium_cli/commands/jobs.py` — `mycelium job submit`/`finalize` use the wallet keypair.
- Recompile `job_board.wasm` with `mycelium-compiler:latest`, redeploy to testnet, update `mycelium.toml [jobs].board_address`. (v0.1.0 escrow instances unaffected.)
- Tests: extend `sdk/tests/test_jobs.py` — unauthorized `submit_proof`/`finalize` rejected; happy path still settles. Add to `test_live_testnet.py`.

**Verification:** on testnet, post + claim with agent A; `submit_proof`/`finalize` signed by an unrelated key must fail; signed by claimant/poster must succeed and settle.

---

## Priority 1 — Off-chain Indexer (Firestore, hosted; build before the pitch)

Design in `vision/01-offchain-indexer.md`. Chain stays source-of-truth; the indexer is a verifiable cache that turns discovery from an O(N), retention-bounded event-scan (`sdk/mycelium_sdk/hive.py:93` `discover_agents`) into an O(1) searchable lookup over full history. **No new on-chain code** — contracts already emit `agent_registered`, the `job_*` events, `swarm_joined`, and escrow `escrow_locked/released/split/refunded`.

**Build steps:**
1. **Extract shared event logic** — lift the `getEvents` paging loop + `_parse_registration_event` from `sdk/mycelium_sdk/hive.py` into a new `sdk/mycelium_sdk/events.py` (cursor-aware, generic over topic), reused by both SDK and the indexer worker. Reuse `mycelium_sdk.rpc.with_retry` for transient RPC errors.
2. **New `indexer/` package** (sibling to `sdk/`, `cli/`), **co-located with the existing FastAPI IDE backend so it reuses the Firebase Admin SDK already initialized in `ide/backend`**:
   - `indexer/worker.py` — cursor-tracked ingest loop. Stores the `(last_ledger, last_event_id)` cursor in a Firestore doc; calls `getEvents` from the cursor forward; idempotent upserts keyed on event id `(ledger, tx_index, event_index)` as the Firestore document id (re-ingest = overwrite, safe). One `resolve_agent` sim per newly-seen name, then cached. Backfill mode `--from-ledger N`.
   - **Firestore collections** (replacing the Postgres schema in `vision/01` §3b):
     - `agents/{name}` → `{address, capability_tags[], endpoint, model, role, description, reputation, first_seen_ledger, last_update_ledger}`. Capability search via Firestore `array-contains` on `capability_tags`; reputation/status via composite indexes (declare them in `firestore.indexes.json`).
     - `jobs/{job_id}` → `{poster, bounty, token, mode, status, escrow, deadline, spec_hash, posted_ledger}`. Query by `status`/`mode`/`min_bounty`.
     - `jobs/{job_id}/members/{agent}` (subcollection) → `{share_bps}`.
     - `settlements/{event_id}` → `{escrow, job_id, amount, token, kind, ledger}` (volume metrics → business-model dashboard in `vision/03`).
     - `indexer_meta/cursor` → `{last_ledger, last_event_id}`.
   - `indexer/api.py` — FastAPI read API (new routes on the existing backend app or a sibling service): `GET /agents` (capability + min_reputation filters, Firestore cursor pagination via `start_after`), `/agents/{name}`, `/jobs`, `/jobs/{id}`, `/stats`. Each response carries `source_contract` + `as_of_ledger` for client-side verification. Reuses auth/CORS/Firebase patterns from `ide/backend/main.py`.
3. **Capability search** — start with **Option A** (no contract change): the SDK already knows plaintext tags (`HiveClient._compute_capability_hash`, `hive.py:252`); have `register` also publish the tag list (in the event payload or to the agent endpoint) so the worker stores `capability_tags[]` for `array-contains`. (Option B — a `capabilities` event topic in the DSL — is a later, backward-compatible enhancement.)
4. **SDK integration** — `HiveClient.__init__` gains `indexer_url` (default = hosted Mycelium indexer, like `DEFAULT_COMPILE_URL` in `constants.py:46`); `discover_agents(prefer_indexer=True)` tries the indexer HTTP path, falls back to the on-chain event-scan on failure. Add `verify=True` to re-`resolve_agent` returned names on-chain (DB speed, chain trust).
5. **CLI** — `mycelium agents`/`discover` (`cli/mycelium_cli/commands/discover.py`) uses the hosted indexer when reachable (instant), falls back to event-scan offline.
6. **IDE** — wire the `/jobs` route + agent feed to the read API instead of raw event scans.

**Access model:** the SDK/CLI default to the **hosted** indexer URL; no download required. Advanced users can run `indexer/worker.py` + `indexer/api.py` against their own Firebase project and override the URL (sovereign/decentralized path).

**Verification:** register agents on testnet → appear via `GET /agents?capability=...` instantly; wipe the Firestore collections + re-backfill → identical state; kill the worker mid-run → restart resumes from the cursor doc with no dupes; `discover_agents` with the indexer URL down → falls back to event-scan and still returns agents.

---

## Priority 2 — Persistent Agent Memory (build before the pitch)

Design in `vision/02-persistent-agent-memory.md`. **Memory stays off-chain; only a tiny commitment goes on-chain.** Per-agent on-chain footprint is constant and tiny regardless of memory size.

**Build steps:**
1. **`memory_anchor.py`** (root, Mycelium DSL — mirrors `hive_registry.py`): `set_anchor(owner, memory_root, uri, acl)` with `owner.require_auth()` + monotonic `version`; `get_anchor(owner) -> Map`. Emits `memory_anchored {owner, version}` (the indexer indexes "latest anchor per agent"). Reuses only v0.1.0 DSL primitives (`require_auth`, `Bytes`, events, storage keys) — no compiler changes. Compile, deploy to testnet, record id.
2. **`sdk/mycelium_sdk/memory/`** — new subpackage behind one interface `AgentMemory(ctx, backend="auto")`:
   - `remember(content, tags)` / `recall(query, k)` — off-chain, no chain tx.
   - `anchor()` — compute `memory_root` (flat content hash to start; Merkle root is a v2 enhancement), call `set_anchor` on-chain.
   - `rehydrate()` / `verify()` — read anchor → fetch blob from `uri` → recompute hash → compare to `memory_root`; reject on mismatch; honor `version` for rollback protection.
   - `LocalVectorBackend` — SQLite + a small local embedding model (zero-dependency, offline, OSS default).
   - `SupermemoryBackend` — Supermemory keyed by the agent's `G-address` as `containerTag` (ROADMAP §4); the managed/cloud path (revenue surface in `vision/03`).
3. **Anchoring policy hooks** — anchor at job-completion + periodic heartbeat (the cost knob), not per-write.
4. **CLI (optional)** — `mycelium memory anchor` / `verify`.

**Verification:** portability demo — write+anchor on machine A; on machine B `rehydrate()` reads the anchor, fetches, verifies the root, resumes; tamper the blob → `verify()` returns false. (Doubles as a strong pitch demo for stateless/serverless agents.)

---

## Priority 3 — Money-path validation hardening (do alongside P1/P2)

Close input-validation gaps in fund paths (defense-in-depth; escrow re-validates on-chain, but reject early with clear errors).

- `sdk/mycelium_sdk/x402/settlement.py` `create_locked_escrow` — reject `amount_xlm <= 0` and amounts above the i128 ceiling before stroop conversion.
- `sdk/mycelium_sdk/jobs.py` `post_job` — reject `bounty_xlm <= 0`.
- `split_release` (settlement.py) — already checks `sum(bps)==10000`; also reject any `bps <= 0` and empty share lists.
- `join_swarm` SDK path / `mycelium job join` — validate `0 < share_bps <= 10000` client-side (contract checks only the upper bound, line 119).
- `list_open_jobs` (`jobs.py:244`) silently `continue`s on any error — keep for read-only discovery but log at debug so failures aren't invisible.

**Verification:** unit tests in `test_x402.py`/`test_jobs.py` asserting each rejection raises a clear `ValueError`; happy path unchanged.

---

## Priority 4 — IDE backend security hardening

The IDE backend is the most exposed surface and the weakest link for mainnet.

1. **Replace weak token encryption** — `ide/backend/auth/security.py:24-27` derives the Fernet key by null-padding `JWT_SECRET_KEY` (shared, no salt, no rotation): if `JWT_SECRET_KEY` leaks, all stored GitHub tokens + user API keys decrypt. Use a dedicated `TOKEN_ENCRYPTION_KEY` (independent of JWT) with proper HKDF derivation, or per-record envelope encryption. Re-encrypt existing `user_credentials` on next login.
2. **Authenticate money/compute endpoints** — `POST /api/deploy` (accepts a `secret_key` in the body; can fund+deploy) and `POST /compile` are unauthenticated. Gate both behind `get_current_user_session` (already used by `/api/agents/scaffold`); add per-user rate limiting on `/compile`. Prefer encrypted-wallet uploads over raw `secret_key` in the body for any mainnet deploy path.
3. **Secrets in transit** — `/api/agents/scaffold` and `/api/deploy` take a passphrase/secret in the body: document HTTPS-only, confirm prod TLS, never log request bodies on these routes.
4. **CORS** — tighten `allow_methods=["*"]` (`main.py:37`) to methods actually used; keep the origin whitelist; make the localhost origin dev-only via env.

**Verification:** unauthenticated `POST /api/deploy` / `/compile` → 401; token re-encryption round-trips with the new key; rotating `JWT_SECRET_KEY` no longer invalidates stored tokens.

---

## Priority 5 — Mainnet network-config hardening (gates the cutover; post-funding OK)

Make mainnet a first-class, safe target. Most plumbing is already network-gated (`constants.py` has both RPC/Horizon/passphrase/SAC sets; `normalize_network()` enforces the switch); the gaps are defaults and guards.

1. **Registry/board address per network** — `HIVEMIND_REGISTRY_ADDRESS` (`constants.py:15`) is testnet-only. Add a mainnet registry address once deployed; select by `network_type` (like `NATIVE_SAC_ADDRESSES`), keeping the `mycelium.toml [registry].hive_registry_address` override (`register.py:33`). Same convention for a mainnet JobBoard address.
2. **Friendbot guard** — `FRIENDBOT_URL` (`constants.py:37`) and `mycelium fund` must hard-refuse on mainnet with a "pre-fund this address" message. Confirm `deploy.py:100-110` (testnet-only Friendbot, ≥5 XLM mainnet check) is the only auto-fund path.
3. **Scaffold defaults** — `sdk/mycelium_sdk/scaffold.py` (lines 65, 109, 154) hardcodes `network_type="testnet"`; make generated `agent.py`/`mycelium.toml` read the target network so a mainnet project doesn't silently run on testnet.
4. **Explicit-network guard** — `AgentContext.read_only`/`__init__` default to testnet (`context.py:85,118`); warn on first mainnet money op and require explicit `--network mainnet` on `deploy`/`pay`/`job`/`deal`.
5. **Fee strategy** — `base_fee=100` is hardcoded (`context.py:271,505`); `prepare_transaction` adjusts, but add a configurable `base_fee` / fee-bump ceiling and a mainnet sanity floor so congested-network txns don't silently fail.

**Verification:** mainnet-config project with an unfunded wallet → `fund` refuses, `deploy` refuses below 5 XLM; funded wallet → deploy+register hit the mainnet registry; scaffolded project reports `network=mainnet` end-to-end.

---

## Priority 6 — Test coverage & compiler-maturity gates (final pre-go-live)

1. **Live mainnet smoke suite** — a gated suite (`MYCELIUM_MAINNET_TESTS=1`) mirroring `test_live_testnet.py` (register/resolve, escrow lock/claim, one job post→finalize) against mainnet with a pre-funded wallet. Run once before go-live, not in CI.
2. **JobBoard auth regression tests** — from P0, permanent in `test_jobs.py`.
3. **Compiler coverage truth** — run the full fixture set (`compiler/Benchmark` stress runner + the ~200/300 WASM fixtures from memory, pass rate ~129/300). Capture the actual failure list, categorize buckets (unsupported syntax / type gaps / codegen bugs), and **document known limitations** so users don't ship a contract that compiles in one path and traps in another (the v0.2.0 escrow `initialize` trap is the cautionary example). Documentation + triage, not a full compiler push.

**Verification:** mainnet smoke suite passes once; compiler-limitations doc lists failing buckets with reproduction.

---

## Suggested execution order

1. **P0** — JobBoard auth fix (blocking; smallest, highest risk-reduction). Recompile + redeploy + tests.
2. **P1** — Indexer on Firestore (pre-pitch headline; extract `events.py` first → worker → API → SDK/CLI/IDE wiring).
3. **P2** — Agent memory (pre-pitch; `memory_anchor.py` → `AgentMemory` + local backend → Supermemory → portability demo).
4. **P3** — Money-path validation (cheap; alongside P1/P2).
5. **P4** — IDE backend security (before any mainnet deploy through the IDE).
6. **P5** — Network-config hardening (gates the mainnet switch; post-funding OK).
7. **P6** — Test/compiler gates (final pre-go-live).

P0, P3, P4 are **mainnet blockers**. P1, P2 are **pitch deliverables** (P1's `settlements` collection powers the `vision/03` volume dashboard). P5/P6 gate the actual cutover.

## Critical files (by area)

- **Contracts:** `job_board_contract.py` (auth), new `memory_anchor.py`; recompile via `mycelium-compiler:latest`.
- **SDK:** `sdk/mycelium_sdk/{hive.py → events.py extraction, jobs.py, x402/settlement.py, context.py, constants.py, scaffold.py}`; new `sdk/mycelium_sdk/memory/`.
- **Indexer (new, Firebase-backed):** `indexer/{worker.py, api.py}`, `firestore.indexes.json`.
- **CLI:** `cli/mycelium_cli/commands/{jobs.py, discover.py, deploy.py, fund.py}`.
- **IDE backend:** `ide/backend/auth/security.py`, `ide/backend/main.py` (endpoint auth, CORS).
- **Tests/docs:** `sdk/tests/{test_jobs.py, test_x402.py, test_live_testnet.py}`, new mainnet smoke suite, compiler-limitations doc.

## End-to-end verification (mainnet-readiness gate)

A clean run that must all pass before the mainnet shift:
1. Unauthorized `submit_proof`/`finalize` rejected on testnet; claimant/poster path settles.
2. Indexer: register agents → instant `GET /agents` capability search; backfill reproduces state; worker resume is idempotent; SDK falls back when the indexer URL is down.
3. Memory: write+anchor on machine A → rehydrate+verify on machine B; tamper → verify fails.
4. Validation: zero/negative bounty/escrow/share rejected with clear errors.
5. IDE: unauthenticated `/api/deploy` and `/compile` → 401; tokens encrypted with the dedicated key.
6. Network guards: mainnet config refuses Friendbot, enforces funding, hits the mainnet registry, scaffolds with `network=mainnet`.
7. Mainnet smoke suite green once; compiler limitations documented.
