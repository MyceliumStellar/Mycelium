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
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
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
    import tempfile
    import base64
    from mycelium_compiler.codegen import ensure_stellar_cli
    
    # 1. Decode WASM base64
    try:
        wasm_bytes = base64.b64decode(req.wasm_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid base64 encoding: {e}")
        
    # 2. Setup Network and RPC parameters
    is_testnet = req.network == "testnet"
    if is_testnet:
        rpc_url = "https://soroban-testnet.stellar.org"
        network_passphrase = "Test SDF Network ; September 2015"
    else:
        rpc_url = "https://mainnet.sorobanrpc.com"
        network_passphrase = "Public Global Stellar Network ; September 2015"
        
    # 3. Resolve Secret Key (Generate and Friendbot-fund if missing on Testnet)
    secret_key = req.secret_key
    address = None
    generated = False
    
    if not secret_key:
        if not is_testnet:
            raise HTTPException(status_code=400, detail="Secret key is required for mainnet deployment")
        try:
            from stellar_sdk import Keypair
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
            
    # 4. Save WASM bytes to a temp file
    with tempfile.NamedTemporaryFile(suffix=".wasm", delete=False) as tmp:
        tmp.write(wasm_bytes)
        wasm_path = tmp.name
        
    try:
        # Resolve stellar CLI binary path (utilizes the auto-bootstrapper!)
        stellar_bin = ensure_stellar_cli()
        
        cmd = [
            stellar_bin, "contract", "deploy",
            "--wasm", wasm_path,
            "--source-account", secret_key,
            "--rpc-url", rpc_url,
            "--network-passphrase", network_passphrase
        ]
        
        # Run deployment with 45-second timeout (transaction simulation/submission can take a few seconds)
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        
        if res.returncode != 0:
            raise RuntimeError(f"Stellar CLI deploy failed:\nStdout: {res.stdout}\nStderr: {res.stderr}")
            
        contract_id = res.stdout.strip()
        
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
    finally:
        if os.path.exists(wasm_path):
            os.remove(wasm_path)
