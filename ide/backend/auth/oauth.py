import requests
from ide.backend.config import GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET

def exchange_github_code_for_token(code: str) -> str:
    url = "https://github.com/login/oauth/access_token"
    headers = {"Accept": "application/json"}
    payload = {
        "client_id": GITHUB_CLIENT_ID,
        "client_secret": GITHUB_CLIENT_SECRET,
        "code": code
    }
    
    response = requests.post(url, json=payload, headers=headers)
    if response.status_code != 200:
        raise ValueError("Failed to retrieve access token from GitHub")
        
    data = response.json()
    if "access_token" not in data:
        raise ValueError(data.get("error_description", "GitHub authentication failed"))
        
    return data["access_token"]
