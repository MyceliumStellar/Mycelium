"""
`mycelium doctor` — verify the local toolchain and print fixes.

Checks the things that silently break a compile/deploy:
  - stellar-cli present and at the version Mycelium pins (we hit a 25-vs-27
    mismatch in the field),
  - a Rust toolchain with the wasm32 target installed,
  - the Soroban RPC for the configured network is reachable.

Each failed check prints the exact command that fixes it. Exit code is non-zero
if anything is wrong, so it can gate CI.
"""

import re
import shutil
import subprocess
import sys
from typing import Optional

from mycelium_sdk.constants import SOROBAN_RPC_URLS, normalize_network

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


def _check_stellar_cli() -> bool:
    binary = shutil.which("stellar")
    if not binary:
        print(f"  {_NO} stellar-cli   not found on PATH")
        print(f"       fix: cargo install --locked stellar-cli@{PINNED_STELLAR_VERSION}")
        return False
    out = _run(["stellar", "--version"]) or ""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    version = m.group(0) if m else "unknown"
    major = int(m.group(1)) if m else 0
    pinned_major = int(PINNED_STELLAR_VERSION.split(".")[0])
    if major == pinned_major:
        print(f"  {_OK} stellar-cli   {version} ({binary})")
        return True
    print(f"  {_WARN} stellar-cli   {version} — Mycelium pins v{PINNED_STELLAR_VERSION}")
    print(f"       fix: cargo install --locked stellar-cli@{PINNED_STELLAR_VERSION}")
    return False


def _check_rust() -> bool:
    rustc = _run(["rustc", "--version"])
    if not rustc:
        print(f"  {_NO} rust          rustc not found")
        print("       fix: curl https://sh.rustup.rs -sSf | sh")
        return False
    print(f"  {_OK} rust          {rustc}")

    installed = _run(["rustup", "target", "list", "--installed"]) or ""
    if WASM_TARGET in installed:
        print(f"  {_OK} wasm target   {WASM_TARGET} installed")
        return True
    print(f"  {_NO} wasm target   {WASM_TARGET} missing")
    print(f"       fix: rustup target add {WASM_TARGET}")
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
    print("\nMycelium doctor — toolchain check\n")
    results = [
        _check_stellar_cli(),
        _check_rust(),
        _check_rpc(network),
    ]
    ok = all(results)
    print(f"\n{'✓ All checks passed.' if ok else '✗ Some checks failed — see fixes above.'}\n")
    if not ok:
        sys.exit(1)
    return ok
