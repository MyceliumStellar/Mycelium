from dotenv import load_dotenv
import os

# Load local .env environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "MOCK_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "MOCK_CLIENT_SECRET")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "SUPER_SECRET_HMAC_KEY_FOR_JWT_SIGNING")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
GITHUB_REDIRECT_URI = os.getenv("GITHUB_REDIRECT_URI", "http://localhost:3000/playground")

