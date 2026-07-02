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
from rich.console import Console

console = Console()

_OK = "[bold green]✓[/bold green]"
_NO = "[bold red]✗[/bold red]"
_WARN = "[bold yellow]⚠[/bold yellow]"


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
        console.print(f"  {_WARN} stellar-cli   not installed [dim](optional — only for `compile --local`)[/dim]")
        console.print(f"       [yellow]install:[/yellow] cargo install --locked stellar-cli@{PINNED_STELLAR_VERSION}")
        return
    out = _run(["stellar", "--version"]) or ""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    version = m.group(0) if m else "unknown"
    major = int(m.group(1)) if m else 0
    pinned_major = int(PINNED_STELLAR_VERSION.split(".")[0])
    if major == pinned_major:
        console.print(f"  {_OK} stellar-cli   [green]{version}[/green] ({binary}) — [bold green]local compile available[/bold green]")
    else:
        console.print(f"  {_WARN} stellar-cli   [yellow]{version}[/yellow] — Mycelium pins v{PINNED_STELLAR_VERSION} for local compile")
        console.print(f"       [yellow]fix:[/yellow] cargo install --locked stellar-cli@{PINNED_STELLAR_VERSION}")


def _check_rust() -> None:
    """Optional: report local Rust + wasm32 target (only for `compile --local`)."""
    rustc = _run(["rustc", "--version"])
    if not rustc:
        console.print(f"  {_WARN} rust          not installed [dim](optional — only for `compile --local`)[/dim]")
        if sys.platform == "win32":
            console.print("       [yellow]install:[/yellow] Download and run rustup-init.exe from https://rustup.rs/")
        else:
            console.print("       [yellow]install:[/yellow] curl https://sh.rustup.rs -sSf | sh")
        return
    console.print(f"  {_OK} rust          [green]{rustc}[/green]")

    installed = _run(["rustup", "target", "list", "--installed"]) or ""
    if WASM_TARGET in installed:
        console.print(f"  {_OK} wasm target   [green]{WASM_TARGET}[/green] installed — [bold green]local compile available[/bold green]")
    else:
        console.print(f"  {_WARN} wasm target   [yellow]{WASM_TARGET}[/yellow] missing (optional)")
        console.print(f"       [yellow]fix:[/yellow] rustup target add {WASM_TARGET}")


def _check_compile_endpoint() -> bool:
    """Required: the hosted compile endpoint must be reachable (default path)."""
    import requests

    # GET the backend root — the /compile route only accepts POST, but a
    # reachable host returning any HTTP response proves connectivity.
    base = COMPILE_URL.rsplit("/compile", 1)[0] or COMPILE_URL
    try:
        requests.get(base, timeout=15)
        console.print(f"  {_OK} compile       hosted endpoint [green]reachable[/green] ({COMPILE_URL})")
        return True
    except Exception as e:
        console.print(f"  {_NO} compile       hosted endpoint [bold red]unreachable[/bold red]: {e}")
        console.print(f"       [yellow]fix:[/yellow] check connectivity to {COMPILE_URL}, set MYCELIUM_COMPILE_URL, "
                      f"or install a local toolchain and use `compile --local`")
        return False


def _check_rpc(network: str) -> bool:
    url = SOROBAN_RPC_URLS[network]
    try:
        from stellar_sdk import SorobanServer

        seq = SorobanServer(url).get_latest_ledger().sequence
        console.print(f"  {_OK} rpc           [cyan]{network}[/cyan] [green]reachable[/green] (ledger {seq})")
        return True
    except Exception as e:
        console.print(f"  {_NO} rpc           [cyan]{network}[/cyan] [bold red]unreachable[/bold red]: {e}")
        console.print(f"       [yellow]fix:[/yellow] check connectivity to {url}")
        return False


def run_doctor(network: Optional[str] = None) -> bool:
    network = normalize_network(network or get_value("onchain", "network", "testnet"))
    console.print("\n[bold green]Mycelium Doctor[/bold green] — connectivity check\n")

    # Required checks gate the exit code; optional ones are informational only.
    required = [
        _check_compile_endpoint(),
        _check_rpc(network),
    ]
    console.print("\n  [bold cyan]optional[/bold cyan] — local compile (not needed for the default workflow):")
    _check_stellar_cli()
    _check_rust()

    ok = all(required)
    if ok:
        console.print(f"\n[bold green]✓ All required checks passed.[/bold green]\n")
    else:
        console.print(f"\n[bold red]✗ Some required checks failed — see fixes above.[/bold red]\n")
    if not ok:
        sys.exit(1)
    return ok
