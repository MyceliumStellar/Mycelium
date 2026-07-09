"""
mycelium update — check for and install the latest mycelium-stellar release.

Queries PyPI for the latest published version. If a newer version is available,
prompts the user (unless --yes is passed) and runs `pip install --upgrade`
automatically.
"""

import subprocess
import sys

import typer


def _get_current_version() -> str:
    """Return the installed version of mycelium-stellar, avoiding circular imports."""
    try:
        from importlib.metadata import version
        return version("mycelium-stellar")
    except Exception:
        return "0.0.0"

PYPI_PACKAGE = "mycelium-stellar"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PYPI_PACKAGE}/json"


def _fetch_latest_version() -> str | None:
    """Query the PyPI JSON API for the latest version of mycelium-stellar."""
    try:
        import requests
        resp = requests.get(PYPI_JSON_URL, timeout=10)
        resp.raise_for_status()
        return resp.json()["info"]["version"]
    except Exception:
        return None


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a PEP-440 version string into a comparable tuple of ints."""
    parts = []
    for segment in v.split("."):
        try:
            parts.append(int(segment))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def run_update(yes: bool = False) -> None:
    """Check PyPI for the latest version and auto-update if newer."""
    _current_version = _get_current_version()

    typer.echo(f"📦  Current version : {_current_version}")
    typer.echo(f"🔍  Checking PyPI for the latest {PYPI_PACKAGE} release...")

    latest = _fetch_latest_version()
    if latest is None:
        typer.echo("❌  Could not reach PyPI. Check your internet connection and try again.")
        raise typer.Exit(code=1)

    typer.echo(f"📡  Latest version  : {latest}")

    if _parse_version(latest) <= _parse_version(_current_version):
        typer.echo(f"✅  You are already on the latest version ({_current_version}). Nothing to do.")
        return

    typer.echo(f"\n🚀  A newer version is available: {_current_version} → {latest}")

    if not yes:
        proceed = typer.confirm("Do you want to upgrade now?", default=True)
        if not proceed:
            typer.echo("Skipped. Run `pip install --upgrade mycelium-stellar` manually when ready.")
            return

    typer.echo(f"\n⬆️   Upgrading {PYPI_PACKAGE} to {latest}...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--upgrade", f"{PYPI_PACKAGE}=={latest}"],
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    except subprocess.CalledProcessError as exc:
        typer.echo(f"\n❌  Upgrade failed (exit code {exc.returncode}). Try manually:\n"
                   f"    pip install --upgrade {PYPI_PACKAGE}=={latest}")
        raise typer.Exit(code=1)

    typer.echo(f"\n✅  Successfully upgraded to mycelium-stellar {latest}!")
    typer.echo("    Restart any running mycelium processes to use the new version.")
