import firebase_admin
from firebase_admin import credentials
from firebase_admin import db as rtdb
import os
import json

db_url = os.getenv("FIREBASE_DATABASE_URL", "https://mycelium-9a2ed-default-rtdb.firebaseio.com/")
cred_json_str = os.getenv("FIREBASE_CREDENTIALS_JSON")
cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH")
key_fallback_path = os.path.join(os.path.dirname(__file__), "..", "mycelium-9a2ed-firebase-adminsdk-fbsvc-2f9ea3cf24.json")

if not firebase_admin._apps:
    cred = None
    if cred_json_str:
        try:
            cred_info = json.loads(cred_json_str)
            cred = credentials.Certificate(cred_info)
            print("[Firebase] Initialized with credentials from FIREBASE_CREDENTIALS_JSON environment variable.")
        except Exception as e:
            print(f"[Firebase] Error parsing FIREBASE_CREDENTIALS_JSON: {e}")

    if not cred and cred_path:
        if os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            print(f"[Firebase] Initialized with credentials file from {cred_path}.")
        else:
            print(f"[Firebase] Credentials file not found at path {cred_path}.")

    if not cred and os.path.exists(key_fallback_path):
        cred = credentials.Certificate(key_fallback_path)
        print(f"[Firebase] Initialized with fallback credentials file from {key_fallback_path}.")

    if cred:
        firebase_admin.initialize_app(cred, {
            'databaseURL': db_url
        })
    else:
        # Fallback to Application Default Credentials / ENV for safety
        print("[Firebase] No certificate found. Falling back to default initialization.")
        firebase_admin.initialize_app(options={
            'databaseURL': db_url
        })

def get_db():
    # Returns the Realtime Database reference module
    yield rtdb
