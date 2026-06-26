# Deploying the Mycelium off-chain indexer

The indexer is a single Docker image that serves the read API and (optionally)
runs the Firestore ingest worker in a background thread. It's built from the
**repo root** because it installs the local `mycelium-sdk` and `mycelium-cli`
packages.

## What it does

- `GET /agents`, `/agents/{name}`, `/jobs`, `/jobs/{id}`, `/stats` — O(1)
  discovery over a Firestore cache of on-chain events.
- `POST /agents/{name}/capabilities` — records plaintext capability tags, but
  only if they hash to the agent's on-chain `capability_hash` (trustless).
- With `RUN_INDEXER_WORKER=1`, ingests `agent_registered`, `job_*`,
  `swarm_joined`, and `escrow_*` events from a persisted cursor.

## Prerequisites (one-time)

1. **Firestore (default) database created in Native mode.** This is separate
   from the project's existing Realtime Database (`*-default-rtdb`). Firebase
   console → **Firestore Database → Create database → Native mode** → pick a
   region. Without it, ingest fails with
   `404 The database (default) does not exist for project …` (the error links
   straight to the setup page).

   **Database id gotcha:** newer ("Enterprise edition") projects create the
   first database with id **`default`**, not the legacy **`(default)`** the
   client library assumes — so even with the db created you get the same 404.
   Set **`FIRESTORE_DATABASE_ID`** to your db's actual id (this project:
   `default`). Check yours at *console → Firestore → Databases*, or
   `GET https://firestore.googleapis.com/v1/projects/<proj>/databases`.
2. **A service-account key JSON.** You can reuse the one already in the repo
   (`ide/backend/mycelium-9a2ed-firebase-adminsdk-fbsvc-2f9ea3cf24.json`, same
   project) or create a fresh key: Firebase console → Project settings → Service
   accounts → *Generate new private key*.
3. **Composite indexes.** The capability+reputation and status/mode+bounty
   queries need the indexes in `firestore.indexes.json` (repo root). Either:
   - `npm i -g firebase-tools && firebase deploy --only firestore:indexes`, or
   - just run a query once — Firestore returns a console link that creates the
     exact index in one click.

## Build & test locally

```bash
# from repo root
docker build -f indexer/Dockerfile -t mycelium-indexer:latest .

docker run --rm -p 8080:8080 \
  -e FIREBASE_CREDENTIALS_JSON="$(cat path/to/serviceAccount.json)" \
  -e FIRESTORE_DATABASE_ID=default \
  -e MYCELIUM_BOARD_ADDRESS=CAIGNIJBUA4GKKJBIO27JOAELZQ4KA7AYMB2F5C3W2D3DGQANZZCJGEH \
  -e RUN_INDEXER_WORKER=1 \
  mycelium-indexer:latest

curl localhost:8080/healthz          # {"ok":true}
curl localhost:8080/stats            # after the worker has run a pass
curl "localhost:8080/agents?capability=vision"
```

## Deploy to Render

**Option A — Blueprint (recommended).** The repo has `render.yaml`.
1. Render dashboard → **New → Blueprint** → connect this repo.
2. Render detects `render.yaml` and proposes the `mycelium-indexer` web service.
3. When prompted, paste the **whole service-account JSON** as the value of
   `FIREBASE_CREDENTIALS_JSON` (it's marked `sync: false`, so it's never in git).
4. Create. Render builds `indexer/Dockerfile` and gives you a URL like
   `https://mycelium-indexer.onrender.com`.

**Option B — manual.** New → **Web Service** → this repo →
- Runtime: **Docker**
- Dockerfile Path: `indexer/Dockerfile`
- Docker Build Context Directory: `.` (repo root)
- Health Check Path: `/healthz`
- Env vars: `FIREBASE_CREDENTIALS_JSON` (the JSON), `RUN_INDEXER_WORKER=1`,
  `MYCELIUM_NETWORK=testnet`, `MYCELIUM_BOARD_ADDRESS=CAIGNIJB…ZZCJGEH`.

Render injects `$PORT`; the container already binds it.

### Free-plan note
Render's free web service **sleeps after ~15 min idle**, which also pauses the
in-process worker. That's fine for a demo: on the next request it wakes and the
worker resumes from its Firestore cursor (no gaps, no dupes), and the SDK/CLI
fall back to the on-chain scan while it's asleep. For a *continuously* fresh
cache, use a paid instance, or split the worker into a separate Render
**Background Worker** with start command `python -m indexer.worker` (same image
and env, drop `RUN_INDEXER_WORKER` on the web service).

## After deploy — point clients at it

Set the indexer URL so the SDK/CLI use it (otherwise they keep falling back to
the chain scan):

- Quick / per-environment: `export MYCELIUM_INDEXER_URL=https://<your-service>.onrender.com`
- Permanent default: edit `DEFAULT_INDEXER_URL` in
  `sdk/mycelium_sdk/constants.py` to your Render URL, and the same literal in
  `ide/frontend` if the bounty UI should read it.

Verify end-to-end:

```bash
export MYCELIUM_INDEXER_URL=https://<your-service>.onrender.com
mycelium agents                 # "Querying indexer ..." then instant results
curl "$MYCELIUM_INDEXER_URL/agents"
```
