from dotenv import load_dotenv
import os

# Load local .env environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "MOCK_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "MOCK_CLIENT_SECRET")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "SUPER_SECRET_HMAC_KEY_FOR_JWT_SIGNING")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# Dedicated key for at-rest encryption of stored secrets (GitHub tokens, user
# API keys) — INDEPENDENT of JWT_SECRET_KEY so that a leak of the JWT signing
# key does not also decrypt every stored credential. Set TOKEN_ENCRYPTION_KEY
# to a long random string in production (e.g. `openssl rand -base64 48`).
TOKEN_ENCRYPTION_KEY = os.getenv("TOKEN_ENCRYPTION_KEY")
if not TOKEN_ENCRYPTION_KEY:
    # Dev fallback: distinct from JWT so the two keys are never the same value,
    # but loud so it never silently ships to production.
    import warnings
    TOKEN_ENCRYPTION_KEY = "DEV_ONLY_TOKEN_ENCRYPTION_KEY_set_TOKEN_ENCRYPTION_KEY_in_prod"
    warnings.warn(
        "TOKEN_ENCRYPTION_KEY is unset — using an insecure dev default. "
        "Set TOKEN_ENCRYPTION_KEY (independent of JWT_SECRET_KEY) in production.",
        RuntimeWarning,
    )
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
GITHUB_REDIRECT_URI = os.getenv("GITHUB_REDIRECT_URI", "http://localhost:3000/playground")

