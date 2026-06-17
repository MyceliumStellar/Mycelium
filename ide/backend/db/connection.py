import firebase_admin
from firebase_admin import credentials
from firebase_admin import db as rtdb
import os

key_path = os.path.join(os.path.dirname(__file__), "..", "mycelium-9a2ed-firebase-adminsdk-fbsvc-2f9ea3cf24.json")
db_url = "https://mycelium-9a2ed-default-rtdb.firebaseio.com/"

if not firebase_admin._apps:
    if os.path.exists(key_path):
        cred = credentials.Certificate(key_path)
        firebase_admin.initialize_app(cred, {
            'databaseURL': db_url
        })
    else:
        # Fallback to Application Default Credentials / ENV for safety
        print(f"[Firebase] Service account file not found at {key_path}. Falling back to default initialization.")
        firebase_admin.initialize_app(options={
            'databaseURL': db_url
        })

def get_db():
    # Returns the Realtime Database reference module
    yield rtdb
