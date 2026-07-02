"""
`mycelium doctor` — verify connectivity and print fixes.

Mycelium's default happy path is **zero-toolchain**: compile runs on the hosted
backend and deploy is pure-Python signed transactions, so neither Rust nor
stellar-cli is required. doctor's hard checks reflect that:
  - the hosted compile endpoint is reachable,
  - the Soroban RPC for the configured network is reachable.

stellar-cli and a local Rust/wasm32 toolchain are reported as **optional**
"local compile" capabilities — their absence is informational, never a failure.

Each failed check prints the exact command that fixes it. Exit code is non-zero
only if a *required* check fails, so it can gate CI.
"""

import re
import shutil
import subprocess
import sys
from typing import Optional

from mycelium_sdk.constants import COMPILE_URL, SOROBAN_RPC_URLS, normalize_network

from mycelium_cli.config import get_value

# Must track compiler/.../core.py::ensure_stellar_cli (the version we bundle).
PINNED_STELLAR_VERSION = "27.0.0"
WASM_TARGET = "wasm32-unknown-unknown"

_OK = "✓"
_NO = "✗"
_WARN = "⚠"


def _run(cmd: list[str]) -> Optional[str]:
    """Run `cmd`, returning stripped stdout, or None if it isn't runnable."""
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return (res.stdout or res.stderr).strip()


def _check_stellar_cli() -> None:
    """Optional: report local stellar-cli (only needed for `compile --local`)."""
    binary = shutil.which("stellar")
    if not binary:
        print(f"  {_WARN} stellar-cli   not installed (optional — only for `compile --local`)")
        print(f"       install: cargo install --locked stellar-cli@{PINNED_STELLAR_VERSION}")
        return
    out = _run(["stellar", "--version"]) or ""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    version = m.group(0) if m else "unknown"
    major = int(m.group(1)) if m else 0
    pinned_major = int(PINNED_STELLAR_VERSION.split(".")[0])
    if major == pinned_major:
        print(f"  {_OK} stellar-cli   {version} ({binary}) — local compile available")
    else:
        print(f"  {_WARN} stellar-cli   {version} — Mycelium pins v{PINNED_STELLAR_VERSION} for local compile")
        print(f"       fix: cargo install --locked stellar-cli@{PINNED_STELLAR_VERSION}")


def _check_rust() -> None:
    """Optional: report local Rust + wasm32 target (only for `compile --local`)."""
    rustc = _run(["rustc", "--version"])
    if not rustc:
        print(f"  {_WARN} rust          not installed (optional — only for `compile --local`)")
        if sys.platform == "win32":
            print("       install: Download and run rustup-init.exe from https://rustup.rs/")
        else:
            print("       install: curl https://sh.rustup.rs -sSf | sh")
        return
    print(f"  {_OK} rust          {rustc}")

    installed = _run(["rustup", "target", "list", "--installed"]) or ""
    if WASM_TARGET in installed:
        print(f"  {_OK} wasm target   {WASM_TARGET} installed — local compile available")
    else:
        print(f"  {_WARN} wasm target   {WASM_TARGET} missing (optional)")
        print(f"       fix: rustup target add {WASM_TARGET}")


def _check_compile_endpoint() -> bool:
    """Required: the hosted compile endpoint must be reachable (default path)."""
    import requests

    # GET the backend root — the /compile route only accepts POST, but a
    # reachable host returning any HTTP response proves connectivity.
    base = COMPILE_URL.rsplit("/compile", 1)[0] or COMPILE_URL
    try:
        requests.get(base, timeout=15)
        print(f"  {_OK} compile       hosted endpoint reachable ({COMPILE_URL})")
        return True
    except Exception as e:
        print(f"  {_NO} compile       hosted endpoint unreachable: {e}")
        print(f"       fix: check connectivity to {COMPILE_URL}, set MYCELIUM_COMPILE_URL, "
              f"or install a local toolchain and use `compile --local`")
        return False


def _check_rpc(network: str) -> bool:
    url = SOROBAN_RPC_URLS[network]
    try:
        from stellar_sdk import SorobanServer

        seq = SorobanServer(url).get_latest_ledger().sequence
        print(f"  {_OK} rpc           {network} reachable (ledger {seq})")
        return True
    except Exception as e:
        print(f"  {_NO} rpc           {network} unreachable: {e}")
        print(f"       fix: check connectivity to {url}")
        return False


def run_doctor(network: Optional[str] = None) -> bool:
    network = normalize_network(network or get_value("onchain", "network", "testnet"))
    print("\nMycelium doctor — connectivity check\n")

    # Required checks gate the exit code; optional ones are informational only.
    required = [
        _check_compile_endpoint(),
        _check_rpc(network),
    ]
    print("\n  optional — local compile (not needed for the default workflow):")
    _check_stellar_cli()
    _check_rust()

    ok = all(required)
    print(f"\n{'✓ All required checks passed.' if ok else '✗ Some required checks failed — see fixes above.'}\n")
    if not ok:
        sys.exit(1)
    return ok
