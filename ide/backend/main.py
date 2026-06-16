from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List

app = FastAPI(title="Mycelium Web IDE API Gateway")

# Enable CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class CompileRequest(BaseModel):
    filename: str
    source_code: str

class CompileResponse(BaseModel):
    success: bool
    wasm_base64: str
    logs: str

@app.get("/")
def read_root():
    return {"message": "Welcome to the Mycelium API Gateway"}

@app.post("/compile", response_model=CompileResponse)
async def compile_endpoint(req: CompileRequest):
    """
    Compilation endpoint running directly on host (without Docker).
    Receives source, parses AST, validates types, compiles to WASM, returns base64 WASM.
    """
    from mycelium_compiler.parser import parse_source
    from mycelium_compiler.validator import validate_ast
    from mycelium_compiler.codegen import generate_wasm
    import base64
    import io
    import sys

    # Capture logs
    log_stream = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = log_stream

    try:
        print("[Compiler Sandbox] Initiating parsing...")
        visitor = parse_source(req.source_code)
        
        print("[Compiler Sandbox] Performing type validations...")
        validate_ast(visitor)
        
        print("[Compiler Sandbox] Generating WASM bytecode...")
        wasm_bytes = generate_wasm(visitor)
        wasm_b64 = base64.b64encode(wasm_bytes).decode("utf-8")
        
        print("[Compiler Sandbox] Compilation succeeded.")
        sys.stdout = old_stdout
        return CompileResponse(success=True, wasm_base64=wasm_b64, logs=log_stream.getvalue())

    except Exception as e:
        print(f"[Compiler Sandbox] Compilation failed: {e}")
        sys.stdout = old_stdout
        return CompileResponse(success=False, wasm_base64="", logs=log_stream.getvalue())

@app.get("/auth/github")
async def github_auth_url():
    # Placeholder for generating GitHub authorization redirect URL
    return {"url": "https://github.com/login/oauth/authorize?client_id=MOCK_CLIENT_ID&scope=repo"}

@app.post("/auth/github/callback")
async def github_auth_callback(code: str):
    # Process token exchange with GitHub OAuth, store securely, set JWT cookie
    return {"access_token": "mock_jwt_cookie_token"}
