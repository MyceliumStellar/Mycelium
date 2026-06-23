import sys
import os

# Ensure project root and subdirectories are in sys.path so imports resolve correctly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "compiler")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "sdk")))

from fastapi import FastAPI, Depends, HTTPException, Header, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import requests
import base64
import uuid

from ide.backend.db.connection import get_db
from ide.backend.sandbox.runner import compile_in_host_sandbox
from ide.backend.auth.security import (
    create_access_token, decode_access_token, encrypt_token, decrypt_token
)
from ide.backend.config import GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, FRONTEND_URL, GITHUB_REDIRECT_URI
import datetime

app = FastAPI(title="Mycelium Web IDE API Gateway")

# Enable CORS for Next.js frontend
origins = ["http://localhost:3000", "https://mycelium.isriz.xyz"]
if FRONTEND_URL:
    origins.append(FRONTEND_URL.rstrip("/"))
origins = list(set(origins))

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup_event():
    import threading
    try:
        from mycelium_compiler.codegen import ensure_stellar_cli
        threading.Thread(target=ensure_stellar_cli, daemon=True).start()
    except Exception as e:
        print(f"[Startup] Failed to initiate stellar-cli bootstrapper: {e}")

class CompileRequest(BaseModel):
    filename: str
    source_code: str

class CompileResponse(BaseModel):
    success: bool
    wasm_base64: str
    logs: str

class RepoCreate(BaseModel):
    name: str

class FileCommitRequest(BaseModel):
    filename: str
    content: str
    sha: Optional[str] = None

class ModelsRequest(BaseModel):
    framework: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None

class ScaffoldRequest(BaseModel):
    project_name: str
    framework: str = "custom"
    model: str = "custom"
    unique_name: Optional[str] = None
    api_key: Optional[str] = None
    wallet_passphrase: Optional[str] = None

# Dependency to authenticate requests via JWT and load the user's decrypted GitHub token
def get_current_user_session(authorization: str = Header(None), db = Depends(get_db)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication token required")
    
    token = authorization.split(" ")[1]
    


    payload = decode_access_token(token)
    if not payload or "user_id" not in payload:
        raise HTTPException(status_code=401, detail="Session expired or invalid token")
        
    user_id = payload["user_id"]
    
    user_data = db.reference("users").child(user_id).get()
    if not user_data:
        raise HTTPException(status_code=401, detail="User not found")
        
    class UserObject:
        def __init__(self, uid, data):
            self.id = uid
            self.github_user_id = data.get("github_user_id")
            self.github_username = data.get("github_username")
            self.avatar_url = data.get("avatar_url")
            
    user = UserObject(user_id, user_data)
        
    cred_data = db.reference("user_credentials").child(user_id).get()
    if not cred_data:
        raise HTTPException(status_code=401, detail="GitHub access token not configured in database")
        
    github_token = decrypt_token(cred_data.get("encrypted_github_token"))
    return {"user": user, "github_token": github_token}


@app.get("/")
def read_root():
    return {"message": "Welcome to the Mycelium API Gateway"}

# OAuth Authorization Redirect URL Generation
@app.get("/auth/github")
def github_auth_url():
    scope = "repo"
    url = f"https://github.com/login/oauth/authorize?client_id={GITHUB_CLIENT_ID}&redirect_uri={GITHUB_REDIRECT_URI}&scope={scope}"
    return {"url": url}

# OAuth Callback Exchange Code for Access Token
@app.post("/auth/github/callback")
def github_auth_callback(code: str, db = Depends(get_db)):
    # 1. Exchange OAuth code for GitHub Access Token
    token_url = "https://github.com/login/oauth/access_token"
    headers = {"Accept": "application/json"}
    payload = {
        "client_id": GITHUB_CLIENT_ID,
        "client_secret": GITHUB_CLIENT_SECRET,
        "code": code
    }
    
    res = requests.post(token_url, json=payload, headers=headers)
    if res.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to retrieve access token from GitHub")
        
    token_data = res.json()
    if "access_token" not in token_data:
        raise HTTPException(
            status_code=400, 
            detail=token_data.get("error_description", "GitHub authentication code exchange failed")
        )
        
    access_token = token_data["access_token"]
    
    # 2. Get User profile details from GitHub API
    user_url = "https://api.github.com/user"
    user_headers = {"Authorization": f"token {access_token}"}
    user_res = requests.get(user_url, headers=user_headers)
    if user_res.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to retrieve profile data from GitHub")
        
    user_data = user_res.json()
    github_user_id = user_data["id"]
    github_username = user_data["login"]
    avatar_url = user_data.get("avatar_url")
    
    # 3. Create or Update User record in Firebase Realtime Database
    users_ref = db.reference("users")
    all_users = users_ref.get() or {}
    
    user_id = None
    for uid, udata in all_users.items():
        if udata.get("github_user_id") == github_user_id:
            user_id = uid
            break
            
    if not user_id:
        user_id = str(uuid.uuid4())
        user_data = {
            "github_user_id": github_user_id,
            "github_username": github_username,
            "avatar_url": avatar_url,
            "created_at": datetime.datetime.utcnow().isoformat()
        }
        users_ref.child(user_id).set(user_data)
    else:
        users_ref.child(user_id).update({
            "github_username": github_username,
            "avatar_url": avatar_url
        })
        
    # Encrypt and store access token
    encrypted_token = encrypt_token(access_token)
    cred_data = {
        "encrypted_github_token": encrypted_token,
        "token_salt": base64.b64encode(b"default_salt").decode("utf-8"),
        "updated_at": datetime.datetime.utcnow().isoformat()
    }
    db.reference("user_credentials").child(user_id).set(cred_data)
    
    # 4. Generate JWT session token for frontend authentication
    jwt_token = create_access_token({"user_id": user_id})
    
    return {
        "access_token": jwt_token,
        "username": github_username,
        "avatar_url": avatar_url
    }

# Session Token Silent Refresh
@app.post("/auth/refresh")
def refresh_session_token(authorization: str = Header(None), db = Depends(get_db)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication token required")
    
    token = authorization.split(" ")[1]
    try:
        from jose import jwt
        from ide.backend.config import JWT_SECRET_KEY, ALGORITHM
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM], options={"verify_exp": False})
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid session token structure")
        
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid session token payload")
        
    user_data = db.reference("users").child(user_id).get()
    if not user_data:
        raise HTTPException(status_code=401, detail="User not found")
        
    new_jwt_token = create_access_token({"user_id": user_id})
    
    return {
        "access_token": new_jwt_token,
        "username": user_data.get("github_username"),
        "avatar_url": user_data.get("avatar_url")
    }

# GIT-BACKED WORKSPACE MANAGEMENT
@app.get("/api/workspaces")
def list_repositories(session = Depends(get_current_user_session)):
    github_token = session["github_token"]


    url = "https://api.github.com/user/repos?type=owner&per_page=100&sort=updated"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json"
    }
    res = requests.get(url, headers=headers)
    if res.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to retrieve repositories list from GitHub")
        
    repos = res.json()
    # Return formatted list
    return [{"name": r["name"], "full_name": r["full_name"], "default_branch": r["default_branch"]} for r in repos]

@app.post("/api/workspaces")
def create_repository(repo_req: RepoCreate, session = Depends(get_current_user_session)):
    github_token = session["github_token"]


    url = "https://api.github.com/user/repos"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json"
    }
    # Create private repo and initialize with README.md so main branch exists immediately
    payload = {
        "name": repo_req.name,
        "private": True,
        "auto_init": True,
        "description": "Scaffolded by Mycelium Web IDE"
    }
    res = requests.post(url, json=payload, headers=headers)
    if res.status_code != 201:
        err_msg = res.json().get("message", "Failed to create repository")
        raise HTTPException(status_code=res.status_code, detail=err_msg)
        
    repo_data = res.json()
    return {"name": repo_data["name"], "message": "Repository created successfully"}

@app.get("/api/workspaces/{repo_name}/files")
def list_repo_files(repo_name: str, session = Depends(get_current_user_session)):
    github_token = session["github_token"]


    username = session["user"].github_username
    url = f"https://api.github.com/repos/{username}/{repo_name}/contents"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json"
    }
    res = requests.get(url, headers=headers)
    if res.status_code == 404:
        raise HTTPException(status_code=404, detail="Repository not found or branch not initialized")
    if res.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to load contents from repository")
        
    contents = res.json()
    
    # We filter only files ending with .py (or standard files like README.md/mycelium.toml)
    files = []
    for item in contents:
        if item["type"] == "file" and (item["name"].endswith(".py") or item["name"] == "mycelium.toml" or item["name"] == "README.md"):
            files.append({"name": item["name"], "sha": item["sha"]})
    return files

@app.get("/api/workspaces/{repo_name}/files/{filename}")
def get_repo_file_content(repo_name: str, filename: str, session = Depends(get_current_user_session)):
    github_token = session["github_token"]


    username = session["user"].github_username
    url = f"https://api.github.com/repos/{username}/{repo_name}/contents/{filename}"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json"
    }
    res = requests.get(url, headers=headers)
    if res.status_code == 404:
        raise HTTPException(status_code=404, detail=f"File {filename} not found in repository")
    if res.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch file content from repository")
        
    file_data = res.json()
    # Decode base64 content
    content_b64 = file_data.get("content", "")
    content = base64.b64decode(content_b64.encode("utf-8")).decode("utf-8")
    
    return {
        "filename": filename,
        "content": content,
        "sha": file_data["sha"]
    }

@app.post("/api/workspaces/{repo_name}/files")
def commit_repo_file(repo_name: str, file_req: FileCommitRequest, session = Depends(get_current_user_session)):
    github_token = session["github_token"]


    username = session["user"].github_username
    url = f"https://api.github.com/repos/{username}/{repo_name}/contents/{file_req.filename}"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    # Base64 encode the code content
    content_b64 = base64.b64encode(file_req.content.encode("utf-8")).decode("utf-8")
    
    payload = {
        "message": f"Save file {file_req.filename} via Mycelium IDE",
        "content": content_b64
    }
    
    # If file exists, we must include the sha hash to update it
    if file_req.sha:
        payload["sha"] = file_req.sha
        
    res = requests.put(url, json=payload, headers=headers)
    if res.status_code not in (200, 201):
        err_data = res.json()
        raise HTTPException(
            status_code=res.status_code,
            detail=err_data.get("message", "Failed to commit changes to GitHub")
        )
        
    commit_data = res.json()
    return {
        "filename": file_req.filename,
        "sha": commit_data["content"]["sha"],
        "message": "File committed successfully to GitHub repository"
    }

# ── In-IDE Agent Creation ────────────────────────────────────────────────────

def _gh_create_repo(github_token: str, name: str) -> dict:
    """Create a private, auto-initialized GitHub repo. Mirrors create_repository."""
    res = requests.post(
        "https://api.github.com/user/repos",
        json={"name": name, "private": True, "auto_init": True,
              "description": "Scaffolded by Mycelium Web IDE"},
        headers={"Authorization": f"token {github_token}",
                 "Accept": "application/vnd.github.v3+json"},
    )
    if res.status_code != 201:
        raise HTTPException(status_code=res.status_code,
                            detail=res.json().get("message", "Failed to create repository"))
    return res.json()


def _gh_commit_file(github_token: str, username: str, repo: str, filename: str, content: str) -> None:
    """Create-or-update a file in `repo`. Mirrors commit_repo_file (fetches sha if present)."""
    url = f"https://api.github.com/repos/{username}/{repo}/contents/{filename}"
    headers = {"Authorization": f"token {github_token}", "Accept": "application/vnd.github.v3+json"}
    payload = {
        "message": f"Scaffold {filename} via Mycelium IDE",
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
    }
    # auto_init created README.md, so it already has a sha — fetch it to update in place.
    existing = requests.get(url, headers=headers)
    if existing.status_code == 200:
        payload["sha"] = existing.json().get("sha")
    res = requests.put(url, json=payload, headers=headers)
    if res.status_code not in (200, 201):
        raise HTTPException(status_code=res.status_code,
                            detail=res.json().get("message", f"Failed to commit {filename}"))


@app.post("/api/models")
def list_models_endpoint(req: ModelsRequest):
    """
    Proxy live model discovery for a provider so the API key never round-trips
    through the browser / hits CORS. Mirrors the CLI's `_select_model`.
    """
    from mycelium_sdk import models as model_discovery

    if not model_discovery.supports_discovery(req.framework):
        return {"framework": req.framework, "models": [], "supports_discovery": False}
    try:
        models = model_discovery.list_models(req.framework, api_key=req.api_key, base_url=req.base_url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"framework": req.framework, "models": models, "supports_discovery": True}


@app.post("/api/agents/scaffold")
def scaffold_agent(req: ScaffoldRequest, session = Depends(get_current_user_session), db = Depends(get_db)):
    """
    Create a new private GitHub repo and commit a full Mycelium agent scaffold
    (mycelium.toml, agent.py, contract.py, .gitignore, README.md) using the
    shared `mycelium_sdk.scaffold` templates. The provider API key is stored
    encrypted server-side (never committed). Optionally generates an encrypted
    wallet (passphrase never stored).
    """
    from mycelium_sdk import scaffold as sc

    unique_name = req.unique_name or req.project_name
    if not sc.validate_unique_name(unique_name):
        raise HTTPException(status_code=400, detail="unique_name must match ^[a-zA-Z0-9_]{3,30}$")
    if req.framework not in sc.VALID_FRAMEWORKS:
        raise HTTPException(status_code=400, detail=f"framework must be one of {sc.VALID_FRAMEWORKS}")

    github_token = session["github_token"]
    username = session["user"].github_username

    # 1. Create the repo (auto-initialized with main branch).
    _gh_create_repo(github_token, req.project_name)

    # 2. Commit the scaffold (shared templates — never drifts from `mycelium init`).
    files = {
        "mycelium.toml": sc.config_to_toml(sc.build_config(req.project_name, req.framework, req.model, unique_name)),
        "contract.py": sc.CONTRACT_TEMPLATE,
        "agent.py": sc.agent_template(req.framework, req.model, unique_name),
        ".gitignore": sc.GITIGNORE,
        "README.md": sc.readme_template(req.project_name, req.framework, req.model, unique_name),
    }
    for filename, content in files.items():
        _gh_commit_file(github_token, username, req.project_name, filename, content)

    # 3. Store the provider API key encrypted server-side (NOT in the repo).
    if req.api_key:
        enc = encrypt_token(req.api_key)
        db.reference("user_credentials").child(session["user"].id).child("agent_api_keys").child(req.project_name).set({
            "framework": req.framework,
            "encrypted_api_key": enc,
            "updated_at": datetime.datetime.utcnow().isoformat(),
        })

    # 4. Optionally generate an encrypted wallet (passphrase never stored).
    wallet_public_key = None
    encrypted_wallet = None
    if req.wallet_passphrase:
        from stellar_sdk import Keypair
        from mycelium_sdk import crypto
        kp = Keypair.random()
        wallet_public_key = kp.public_key
        encrypted_wallet = {"public_key": kp.public_key, **crypto.encrypt_secret(kp.secret, req.wallet_passphrase)}

    return {
        "success": True,
        "repo": req.project_name,
        "files": list(files.keys()),
        "unique_name": unique_name,
        "wallet_public_key": wallet_public_key,
        "encrypted_wallet": encrypted_wallet,
        "api_key_stored": bool(req.api_key),
    }

# Compilation Endpoint (Stateless)
@app.post("/compile", response_model=CompileResponse)
async def compile_endpoint(req: CompileRequest):
    """
    Compilation endpoint running directly on host.
    Invokes the compiler visitor, type checks, and generates WASM.
    """
    res = compile_in_host_sandbox(req.source_code)
    
    wasm_b64 = ""
    if res["success"] and res["wasm_bytes"]:
        wasm_b64 = base64.b64encode(res["wasm_bytes"]).decode("utf-8")
        
    logs = f"--- STDOUT ---\n{res['stdout']}\n--- STDERR ---\n{res['stderr']}"
    return CompileResponse(success=res["success"], wasm_base64=wasm_b64, logs=logs)

class DeployRequest(BaseModel):
    wasm_base64: str
    network: Optional[str] = "testnet" # "testnet" or "mainnet"
    secret_key: Optional[str] = None

@app.post("/api/deploy")
def deploy_contract_endpoint(req: DeployRequest):
    """
    Deploy a contract WASM via pure-Python signed Soroban transactions
    (upload WASM hash → create contract). No stellar-cli / Rust dependency.
    """
    import base64
    from stellar_sdk import Keypair, SorobanServer, Network
    from mycelium_sdk.context import deploy_contract as deploy_contract_bytes

    # 1. Decode WASM base64
    try:
        wasm_bytes = base64.b64decode(req.wasm_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid base64 encoding: {e}")

    # 2. Setup Network and RPC parameters
    is_testnet = req.network == "testnet"
    if is_testnet:
        rpc_url = "https://soroban-testnet.stellar.org"
        network_passphrase = Network.TESTNET_NETWORK_PASSPHRASE
    else:
        rpc_url = "https://mainnet.sorobanrpc.com"
        network_passphrase = Network.PUBLIC_NETWORK_PASSPHRASE

    # 3. Resolve Secret Key (Generate and Friendbot-fund if missing on Testnet)
    secret_key = req.secret_key
    address = None
    generated = False

    if not secret_key:
        if not is_testnet:
            raise HTTPException(status_code=400, detail="Secret key is required for mainnet deployment")
        try:
            kp = Keypair.random()
            secret_key = kp.secret
            address = kp.public_key
            generated = True

            # Fund via Friendbot
            friendbot_res = requests.get(f"https://friendbot.stellar.org/?addr={address}", timeout=15)
            if not friendbot_res.ok:
                raise RuntimeError("Friendbot response was not OK")

            # Wait for ledger commitment (Friendbot transactions take ~3-4 seconds to clear)
            import time
            time.sleep(4)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to generate and fund temporary account: {e}")

    # 4. Deploy via pure-Python signed transactions
    try:
        keypair = Keypair.from_secret(secret_key)
        soroban_rpc = SorobanServer(rpc_url)
        contract_id = deploy_contract_bytes(
            soroban_rpc, keypair, network_passphrase, wasm_bytes
        )

        response = {
            "success": True,
            "contract_id": contract_id,
            "network": req.network
        }
        if generated:
            response["generated_address"] = address
            response["generated_secret"] = secret_key

        return response

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
