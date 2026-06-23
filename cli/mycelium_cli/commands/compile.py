"""
`mycelium compile` — compile the project's contract to Soroban WASM.

Two paths:
  - **remote** (default when no local toolchain is detected): POST the source to
    the hosted `/compile` endpoint (constants.COMPILE_URL, runs the compiler in
    Docker server-side) and write back the returned WASM. Needs zero local Rust
    or stellar-cli install — the zero-toolchain default for new users.
  - **local** (`--local`, or auto when a toolchain is present): run the compiler
    pipeline (parse → validate → generate_wasm) on this machine via the pinned
    stellar-cli 27.0.0 toolchain. For users who already have Rust + stellar-cli.
"""

import base64
import os
import shutil
import sys

from mycelium_sdk.constants import COMPILE_URL

from mycelium_cli.config import get_value


def _has_local_toolchain() -> bool:
    """True if both a Rust compiler and the stellar binary are on PATH."""
    return shutil.which("rustc") is not None and shutil.which("stellar") is not None


def _compile_local(source_code: str, optimize: bool) -> bytes:
    """Compile in-process via the bundled compiler (needs Rust + stellar-cli)."""
    from mycelium_compiler.parser import parse_source
    from mycelium_compiler.validator import validate_ast
    from mycelium_compiler.codegen import generate_wasm

    visitor = parse_source(source_code)
    validate_ast(visitor)
    return generate_wasm(visitor)


def _compile_remote(filename: str, source_code: str) -> bytes:
    """Compile via the hosted `/compile` endpoint; returns the WASM bytes."""
    import requests

    print(f"[compile] No local toolchain in use — compiling remotely via {COMPILE_URL}...")
    try:
        res = requests.post(
            COMPILE_URL,
            json={"filename": filename, "source_code": source_code},
            timeout=120,
        )
    except requests.RequestException as e:
        print(f"❌ Remote compile request failed: {e}")
        print(f"   Set MYCELIUM_COMPILE_URL to point at a reachable backend, or "
              f"install a local toolchain and use `--local`.")
        sys.exit(1)

    if not res.ok:
        print(f"❌ Remote compile failed: HTTP {res.status_code}\n{res.text}")
        sys.exit(1)

    payload = res.json()
    if not payload.get("success"):
        print(f"❌ Compilation failed:\n{payload.get('logs', '(no logs returned)')}")
        sys.exit(1)

    wasm_b64 = payload.get("wasm_base64")
    if not wasm_b64:
        print("❌ Remote compile returned success but no WASM payload.")
        sys.exit(1)
    return base64.b64decode(wasm_b64)


def run_compile(
    file_path: str | None = None,
    output_path: str | None = None,
    optimize: bool = False,
    remote: bool | None = None,
    local: bool = False,
) -> str:
    """
    Compile `file_path` to `output_path`. When omitted, both are read from
    mycelium.toml ([onchain].source_contract / [onchain].target_wasm).

    Path selection:
      - `local=True`  → force the local toolchain.
      - `remote=True` → force the hosted endpoint.
      - neither       → local iff a toolchain is detected, else remote.
    Returns the output path.
    """
    file_path = file_path or get_value("onchain", "source_contract", "contract.py")
    output_path = output_path or get_value("onchain", "target_wasm", "build/contract.wasm")

    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found.")
        sys.exit(1)

    with open(file_path, "r") as f:
        source_code = f.read()

    # Decide the path. Explicit flags win; otherwise auto-detect.
    if local and remote:
        print("Error: pass at most one of --local / --remote.")
        sys.exit(1)
    if local:
        use_remote = False
    elif remote:
        use_remote = True
    else:
        use_remote = not _has_local_toolchain()

    where = "remotely" if use_remote else "locally"
    print(f"Compiling contract {file_path} -> {output_path} ({where})...")

    try:
        if use_remote:
            wasm_bytes = _compile_remote(os.path.basename(file_path), source_code)
        else:
            wasm_bytes = _compile_local(source_code, optimize)
    except SystemExit:
        raise
    except Exception as e:
        print(f"❌ Compilation failed: {e}")
        sys.exit(1)

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_path, "wb") as f_out:
        f_out.write(wasm_bytes)

    size = len(wasm_bytes)
    print(f"✓ Compilation successful! Output: {output_path}")
    print(f"  WASM size: {size:,} bytes ({size / 1024:.2f} KiB)")
    # The release profile already optimizes for size (opt-level "z", LTO).
    if optimize and not use_remote:
        print("  (--optimize: size-optimized release profile is always applied)")
    return output_path
