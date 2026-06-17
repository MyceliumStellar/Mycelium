# Mycelium Web IDE Architecture Guide

This document outlines the architecture, directory layout, database models, and API endpoints of the Mycelium Web IDE.

---

## 📁 Directory Structure

The IDE is organized into a Next.js frontend and a FastAPI backend:

```
ide/
├── backend/
│   ├── auth/
│   │   └── security.py       # JWT session generation, Fernet token encryption
│   ├── db/
│   │   └── connection.py     # Firebase Firestore database client configuration
│   ├── sandbox/
│   │   └── runner.py         # Isolation workspace runner for AST compiler transpiler
│   ├── config.py             # Environment configurations
│   ├── main.py               # FastAPI router and API endpoints
│   └── requirements.txt      # Backend dependencies
└── frontend/
    ├── src/
    │   └── app/
    │       ├── globals.css   # Neon-glow retro stylings and drag resizer classes
    │       ├── layout.tsx    # Next.js layout bootstrap
    │       └── playground/
    │           └── page.tsx  # Core Web IDE components (editor, tabs, wallet connector)
    ├── package.json          # Node dependencies
    └── tsconfig.json         # TypeScript configuration
```

---

## 🗄️ Database References (Firebase Realtime Database)

The database layer utilizes Google Firebase Realtime Database to persist authentication states and user credentials using a hierarchical JSON structure.

### `/users/{user_id}`
Stores authenticated user profiles retrieved from GitHub OAuth.
* `github_user_id` (`int`): GitHub internal account ID.
* `github_username` (`str`): GitHub login handle.
* `avatar_url` (`str`): User avatar link.
* `created_at` (`str`, ISO-8601): Account registration date/time.

### `/user_credentials/{user_id}`
Stores encrypted authentication secrets mapped directly to the user's UUID.
* `encrypted_github_token` (`str`): Symmetric-encrypted (Fernet) GitHub access token.
* `token_salt` (`str`): Cryptographic salt used for validation.
* `updated_at` (`str`, ISO-8601): Last token refresh date/time.

---

## 🔌 API Gateway Endpoints (FastAPI)

All endpoints under `/api` require a valid session JWT passed in the HTTP headers:
`Authorization: Bearer <JWT>`

### Authentication
* `GET /auth/github`
  * **Description:** Generates the authentication URL for GitHub OAuth.
  * **Response:** `{"url": "https://github.com/login/oauth/authorize..."}`
* `POST /auth/github/callback?code=<OAUTH_CODE>`
  * **Description:** Exchanges the GitHub authorization code for an access token, registers/updates the user profile in Firebase Realtime Database, encrypts the access token, and returns a signed user session JWT.
  * **Response:** `{"access_token": "<JWT>", "username": "...", "avatar_url": "..."}`

### Workspace Repositories
* `GET /api/workspaces`
  * **Description:** Lists the active user's GitHub repositories.
  * **Response:** `[{"name": "my-stellar-contract", "full_name": "Srizdebnath/my-stellar-contract", "default_branch": "main"}]`
* `POST /api/workspaces`
  * **Description:** Scaffolds a new private repository on the user's GitHub account, pre-populating it with a standard `README.md`.
  * **Request Body:** `{"name": "repo-name"}`
  * **Response:** `{"name": "repo-name", "message": "Repository created successfully"}`

### File Explorer
* `GET /api/workspaces/{repo_name}/files`
  * **Description:** Fetches file trees from the repository branch, filtering for python scripts (`.py`), project configs (`mycelium.toml`), and documentations.
  * **Response:** `[{"name": "01_simple_storage.py", "sha": "..."}]`
* `GET /api/workspaces/{repo_name}/files/{filename}`
  * **Description:** Retrieves the text content of a target file.
  * **Response:** `{"filename": "01_simple_storage.py", "content": "...", "sha": "..."}`
* `POST /api/workspaces/{repo_name}/files`
  * **Description:** Saves and commits file changes directly to the remote repository.
  * **Request Body:** `{"filename": "contract.py", "content": "...", "sha": "file_sha"}`
  * **Response:** `{"filename": "contract.py", "sha": "new_file_sha", "message": "Commit message"}`

### Compiler
* `POST /compile`
  * **Description:** Compiles a raw Python smart contract input into optimized `.wasm` binary.
  * **Request Body:** `{"filename": "contract.py", "source_code": "..."}`
  * **Response:** `{"success": true, "wasm_base64": "...", "logs": "..."}`
