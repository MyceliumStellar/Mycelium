# Mycelium Web IDE Architecture Guide

The Mycelium Web IDE is a browser-based environment for writing, compiling, and
deploying Python smart contracts to the Stellar/Soroban network. It pairs a
**Next.js frontend** with a **FastAPI backend** that authenticates users through
GitHub, stores their work in GitHub repositories, compiles contracts inside an
isolated **Docker sandbox**, and deploys the resulting WASM on-chain.

This document covers the architecture, directory layout, data model, the
compilation sandbox, and every API endpoint. For the compiler internals see
[compiler.md](./compiler.md).

---

## 🗺️ System Overview

```
┌────────────────────┐        ┌──────────────────────────────────────────┐
│  Next.js frontend  │  HTTPS │            FastAPI backend                 │
│  (playground UI)   │ ─────► │              (main.py)                     │
│  localhost:3000    │  JWT   │            localhost:8000                  │
└────────────────────┘        │                                            │
                              │  auth/      → GitHub OAuth + JWT + Fernet   │
                              │  db/        → Firebase Realtime Database    │
                              │  sandbox/   → Docker compile sandbox        │
                              └───────┬───────────────┬───────────────┬────┘
                                      │               │               │
                              ┌───────▼──────┐ ┌──────▼──────┐ ┌──────▼──────────┐
                              │  GitHub API  │ │  Firebase   │ │ mycelium-       │
                              │ repos/files  │ │  Realtime   │ │ compiler:latest │
                              │              │ │  Database   │ │ (Docker)        │
                              └──────────────┘ └─────────────┘ └──────┬──────────┘
                                                                      │ stellar
                                                              ┌───────▼────────┐
                                                              │ Soroban RPC    │
                                                              │ testnet/mainnet│
                                                              └────────────────┘
```

---

## 📁 Directory Structure

```
ide/
├── backend/
│   ├── auth/
│   │   ├── oauth.py          # GitHub OAuth helpers
│   │   └── security.py       # JWT session tokens + Fernet token encryption
│   ├── db/
│   │   └── connection.py     # Firebase Realtime Database client (get_db)
│   ├── sandbox/
│   │   ├── runner.py         # compile_in_host_sandbox (stable entrypoint)
│   │   └── docker_runner.py  # compile_in_docker_sandbox (isolated container)
│   ├── config.py             # Env-driven configuration (OAuth, JWT, URLs)
│   ├── main.py               # FastAPI app: routes + auth dependency
│   └── requirements.txt      # Backend dependencies
└── frontend/
    ├── src/
    │   └── app/
    │       ├── globals.css    # Neon-glow retro styling, drag-resizer classes
    │       ├── layout.tsx     # Next.js root layout
    │       ├── page.tsx       # Marketing and product landing page with navbar navigation
    │       ├── agent/
    │       │   └── page.tsx   # Swarm agent directory network graph visualization
    │       └── playground/
    │           └── page.tsx   # Full browser Monaco IDE workspace dashboard
    ├── package.json           # Node dependencies
    └── tsconfig.json          # TypeScript configuration
```

> The Web IDE is launched together with the backend via `start.sh` at the repo
> root — see [Running the IDE](#-running-the-ide).

---

## 🗄️ Data Model (Firebase Realtime Database)

The backend persists authentication state in Firebase **Realtime Database**
(accessed through `db.reference(...)` in `db/connection.py`) using a
hierarchical JSON structure.

### `/users/{user_id}`
Authenticated user profiles sourced from GitHub OAuth. `user_id` is a
server-generated UUID.
* `github_user_id` (`int`) — GitHub internal account ID.
* `github_username` (`str`) — GitHub login handle.
* `avatar_url` (`str`) — User avatar URL.
* `created_at` (`str`, ISO-8601) — Account registration timestamp.

### `/user_credentials/{user_id}`
Encrypted authentication secrets, keyed by the same UUID.
* `encrypted_github_token` (`str`) — Fernet symmetric-encrypted GitHub access token.
* `token_salt` (`str`) — Cryptographic salt.
* `updated_at` (`str`, ISO-8601) — Last token refresh timestamp.

GitHub access tokens are **never stored in plaintext** — they are encrypted with
Fernet (`auth/security.py`) before being written, and decrypted only inside the
authenticated request dependency.

---

## 🔐 Authentication Flow

1. Frontend calls `GET /auth/github` to obtain the GitHub authorization URL and
   redirects the user there (scope: `repo`).
2. GitHub redirects back with a `code`; the frontend posts it to
   `POST /auth/github/callback`.
3. The backend exchanges the code for a GitHub access token, fetches the user
   profile, upserts `/users/{user_id}`, encrypts and stores the token in
   `/user_credentials/{user_id}`, and returns a signed **session JWT**.
4. Every `/api/*` call sends `Authorization: Bearer <JWT>`. The
   `get_current_user_session` dependency decodes the JWT, loads the user, and
   **decrypts the GitHub token** for use against the GitHub API.
5. `POST /auth/refresh` mints a fresh JWT from an existing (possibly expired)
   one as long as the user still exists.

JWTs are HS256-signed with `JWT_SECRET_KEY` and expire after
`ACCESS_TOKEN_EXPIRE_MINUTES` (60).

---

## 🧪 Compilation Sandbox

User code is **never compiled on the host**. The `/compile` endpoint calls
`compile_in_host_sandbox` (`sandbox/runner.py`), a thin, stably-named wrapper
that delegates to `compile_in_docker_sandbox` (`sandbox/docker_runner.py`).

The sandbox:

1. Writes the submitted source to a temp dir as `contract.py`.
2. Runs the `mycelium-compiler:latest` image with a hardened profile:

   | Flag | Value | Purpose |
   |------|-------|---------|
   | `--network none` | — | no network access from user code/build |
   | `--memory` | `512m` | RAM quota |
   | `--cpus` | `1.0` | CPU quota |
   | `--rm` | — | container removed after each run |
   | volume mount | `tmpdir:/workspace` | share `contract.py` in, `target.wasm` out |
   | timeout | `30s` | hard wall-clock limit (returns a timeout error) |

3. Reads back `/workspace/target.wasm` on success and returns
   `{success, wasm_bytes, stdout, stderr}`.

On backend startup, a daemon thread calls `ensure_stellar_cli()` to pre-fetch
the `stellar` binary so the first deployment isn't delayed. The compiler image
itself is built/verified by `start.sh`. See [compiler.md](./compiler.md) for the
image internals.

## 🖥️ Frontend Pages & Routing

The Next.js application routes traffic across three distinct user views:

* **`/` (Landing Page)**: The primary branding page, illustrating the core value proposition, interactive CLI emulation terminals, bento feature matrices, and agent architecture overviews.
* **`/agent` (Agent Swarms Network)**: A live visual directory displaying all running agents registered in the Hive Registry. It shows the on-chain Registry address, connects agents as glowing nodes with interactive links/wires, and displays selected agent metadata panels on tap/click.
* **`/playground` (Playground IDE)**: The full Monaco-powered browser environment containing workspace management, code compilation logs, transaction simulation results, and wallet balances.

---

## 🔌 API Reference (FastAPI)

Base URL: `http://localhost:8000`. All `/api/*` endpoints require a session JWT:
`Authorization: Bearer <JWT>`.

### Health
* `GET /` → `{"message": "Welcome to the Mycelium API Gateway"}`

### Authentication
* `GET /auth/github`
  * Generates the GitHub OAuth authorization URL (scope `repo`).
  * **Response:** `{"url": "https://github.com/login/oauth/authorize..."}`
* `POST /auth/github/callback?code=<OAUTH_CODE>`
  * Exchanges the OAuth code for an access token, upserts the user in Firebase,
    encrypts and stores the token, and returns a session JWT.
  * **Response:** `{"access_token": "<JWT>", "username": "...", "avatar_url": "..."}`
* `POST /auth/refresh`  *(header: `Authorization: Bearer <JWT>`)*
  * Issues a new JWT from an existing one (ignores expiry) if the user exists.
  * **Response:** `{"access_token": "<JWT>", "username": "...", "avatar_url": "..."}`

### Workspace Repositories
* `GET /api/workspaces`
  * Lists the user's owned GitHub repositories (up to 100, most-recently-updated first).
  * **Response:** `[{"name": "...", "full_name": "owner/...", "default_branch": "main"}]`
* `POST /api/workspaces`
  * Scaffolds a new **private** repo (`auto_init` with a README so `main` exists).
  * **Request:** `{"name": "repo-name"}`
  * **Response:** `{"name": "repo-name", "message": "Repository created successfully"}`

### File Explorer
* `GET /api/workspaces/{repo_name}/files`
  * Lists files in the repo root, filtered to `*.py`, `mycelium.toml`, and `README.md`.
  * **Response:** `[{"name": "01_simple_storage.py", "sha": "..."}]`
* `GET /api/workspaces/{repo_name}/files/{filename}`
  * Returns the decoded text content of a file.
  * **Response:** `{"filename": "...", "content": "...", "sha": "..."}`
* `POST /api/workspaces/{repo_name}/files`
  * Creates or updates a file and commits it to the repo (include `sha` to update
    an existing file).
  * **Request:** `{"filename": "contract.py", "content": "...", "sha": "<sha-or-null>"}`
  * **Response:** `{"filename": "...", "sha": "<new-sha>", "message": "..."}`

### Compiler
* `POST /compile`  *(stateless — no auth required)*
  * Compiles Python source to a Soroban `.wasm` inside the Docker sandbox.
  * **Request:** `{"filename": "contract.py", "source_code": "..."}`
  * **Response:** `{"success": true, "wasm_base64": "...", "logs": "--- STDOUT ---\n...\n--- STDERR ---\n..."}`
  * On failure `success` is `false`, `wasm_base64` is empty, and `logs` carries
    the compiler/cargo output.

### Deployment
* `POST /api/deploy`
  * Deploys a compiled WASM to Soroban via the `stellar` CLI.
  * **Request:** `{"wasm_base64": "...", "network": "testnet" | "mainnet", "secret_key": "<S...>?"}`
  * **Behavior:**
    * `testnet` → RPC `https://soroban-testnet.stellar.org`. If no `secret_key`
      is supplied, a throwaway keypair is generated and funded via **Friendbot**
      (with a ~4s settle delay) before deploying.
    * `mainnet` → RPC `https://mainnet.sorobanrpc.com`; a `secret_key` is
      **required**.
    * Deployment runs `stellar contract deploy` with a 45s timeout.
  * **Response:** `{"success": true, "contract_id": "C...", "network": "testnet"}`
    plus `generated_address` / `generated_secret` when a temporary testnet
    account was created.

---

## ⚙️ Configuration (`config.py`)

Loaded from `ide/backend/.env` (sensible mock defaults for local dev):

| Variable | Default | Purpose |
|----------|---------|---------|
| `GITHUB_CLIENT_ID` | `MOCK_CLIENT_ID` | GitHub OAuth app client ID |
| `GITHUB_CLIENT_SECRET` | `MOCK_CLIENT_SECRET` | GitHub OAuth app secret |
| `JWT_SECRET_KEY` | dev placeholder | HS256 signing key for session JWTs |
| `FRONTEND_URL` | `http://localhost:3000` | CORS allow-origin |
| `GITHUB_REDIRECT_URI` | `http://localhost:3000/playground` | OAuth callback target |
| `ALGORITHM` | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | JWT lifetime |

CORS is restricted to `FRONTEND_URL` with credentials enabled.

---

## ▶️ Running the IDE

The repo-root `start.sh` orchestrates everything:

1. Verifies the Python `venv` and `uvicorn` are present.
2. Installs frontend `node_modules` if missing.
3. Ensures Docker is running and **builds `mycelium-compiler:latest`** if it
   isn't already present.
4. Frees ports `8000` (backend) and `3000` (frontend).
5. Starts the FastAPI backend (`uvicorn main:app --port 8000 --reload`) with
   `PYTHONPATH` wired to `compiler/`, `sdk/`, and the repo root.
6. Starts the Next.js frontend (`npm run dev`).

```bash
./start.sh
# → open http://localhost:3000/playground
```

`Ctrl+C` cleanly terminates both servers.

---

## 🔗 Related Docs

- [compiler.md](./compiler.md) — the Python→Rust→WASM compiler and its Docker image.
</content>
