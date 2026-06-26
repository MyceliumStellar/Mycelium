"""
Firestore client factory for the indexer.

Reuses the same Firebase Admin credential resolution as the IDE backend
(`ide/backend/db/connection.py`) — env-provided JSON, an env path, or the
bundled service-account key — but returns a **Firestore** client rather than the
Realtime Database handle. The Admin app is a process singleton, so the indexer
worker and the read API share one initialization.
"""

import json
import os

_CRED_ENV_JSON = "FIREBASE_CREDENTIALS_JSON"
_CRED_ENV_PATH = "FIREBASE_CREDENTIALS_PATH"
# Firestore database id. Newer ("Enterprise edition") projects create the first
# database with id `default`, NOT the legacy `(default)` the client library
# assumes — pointing at the wrong id raises `404 The database (default) does not
# exist`. This project's db is `default`; override via FIRESTORE_DATABASE_ID.
_DB_ENV = "FIRESTORE_DATABASE_ID"
_DEFAULT_DB_ID = "default"
# Same bundled service-account key the IDE backend falls back to.
_KEY_FALLBACK = os.path.join(
    os.path.dirname(__file__), "..", "ide", "backend",
    "mycelium-9a2ed-firebase-adminsdk-fbsvc-2f9ea3cf24.json",
)


def _ensure_app():
    import firebase_admin
    from firebase_admin import credentials

    if firebase_admin._apps:
        return

    cred = None
    cred_json = os.getenv(_CRED_ENV_JSON)
    if cred_json:
        try:
            cred = credentials.Certificate(json.loads(cred_json))
        except Exception as e:  # noqa: BLE001
            print(f"[indexer] bad {_CRED_ENV_JSON}: {e}")

    cred_path = os.getenv(_CRED_ENV_PATH)
    if cred is None and cred_path and os.path.exists(cred_path):
        cred = credentials.Certificate(cred_path)

    if cred is None and os.path.exists(_KEY_FALLBACK):
        cred = credentials.Certificate(_KEY_FALLBACK)

    if cred is not None:
        firebase_admin.initialize_app(cred)
    else:
        # Application Default Credentials (e.g. GOOGLE_APPLICATION_CREDENTIALS).
        firebase_admin.initialize_app()


def get_firestore():
    """Return a Firestore client, initializing the Admin app once if needed."""
    from firebase_admin import firestore

    _ensure_app()
    database_id = os.getenv(_DB_ENV, _DEFAULT_DB_ID)
    return firestore.client(database_id=database_id)
