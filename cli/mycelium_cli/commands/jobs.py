"""
`mycelium job …` — drive the Sovereign Job Boards from the console.

A Typer sub-app (registered as the `job` command group in `main.py`) that thin-
wraps `mycelium_sdk.JobBoardClient`, reusing the same wallet load + passphrase
resolution as `deploy` / `register`. The JobBoard contract address defaults from
`mycelium.toml` (`[jobs].board_address`); override with `--board`.

Commands: post, list, claim, assign, join, submit, finalize, status.
"""

import hashlib
import os
import sys
from decimal import Decimal

import typer

from mycelium_cli.config import get_value

DEFAULT_WALLET_PATH = os.path.join(".mycelium", "wallet.json")
PASSPHRASE_ENV_VAR = "MYCELIUM_DECRYPT_KEY"

job_app = typer.Typer(help="Post, claim, and settle on-chain jobs (Sovereign Job Boards).")


def _resolve_passphrase(label: str = "Wallet passphrase") -> str:
    """MYCELIUM_DECRYPT_KEY if set, else prompt — matches the rest of the CLI."""
    env_value = os.environ.get(PASSPHRASE_ENV_VAR)
    if env_value:
        return env_value
    return typer.prompt(label, hide_input=True)


def _board_address(override: str | None) -> str:
    board = override or get_value("jobs", "board_address")
    if not board:
        typer.echo(
            "Error: no JobBoard address. Set [jobs].board_address in mycelium.toml "
            "or pass --board <contract_id>."
        )
        raise typer.Exit(code=1)
    return board


def _client(network: str | None, wallet: str, board: str | None, *, signing: bool):
    """Build a JobBoardClient. Read-only commands skip wallet + passphrase."""
    from mycelium_sdk import AgentContext, JobBoardClient

    network = network or get_value("onchain", "network", "testnet")
    if signing:
        if not os.path.exists(wallet):
            typer.echo(f"Error: wallet {wallet} not found. Run `mycelium newwallet` first.")
            raise typer.Exit(code=1)
        ctx = AgentContext(keypair_path=wallet, network_type=network, passphrase=_resolve_passphrase())
    else:
        ctx = AgentContext.read_only(network_type=network)
    return JobBoardClient(ctx, _board_address(board))


def _spec_hash(spec: str) -> tuple[str, bytes]:
    """
    Resolve `spec` to (spec_uri, spec_hash). If it's a path to a file, hash the
    file contents and use the path as the URI; otherwise treat it as a URI and
    hash the URI string itself.
    """
    if os.path.isfile(spec):
        with open(spec, "rb") as f:
            data = f.read()
        return spec, hashlib.sha256(data).digest()
    return spec, hashlib.sha256(spec.encode("utf-8")).digest()


@job_app.command("post")
def post(
    spec: str = typer.Option(..., "--spec", help="Spec file path or URI (hashed for the proof)"),
    bounty: str = typer.Option(..., "--bounty", help="Bounty in XLM"),
    mode: str = typer.Option("single", "--mode", help="single | swarm"),
    token: str = typer.Option(None, "--token", help="Payment token contract (defaults to native XLM SAC)"),
    deadline: int = typer.Option(86400, "--deadline", help="Refund deadline in seconds"),
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
    board: str = typer.Option(None, "--board", help="JobBoard contract id override"),
):
    """Lock a bounty and post a new job; prints the new job_id."""
    client = _client(network, wallet, board, signing=True)
    spec_uri, spec_hash = _spec_hash(spec)
    try:
        job_id = client.post_job(
            spec_uri=spec_uri, spec_hash=spec_hash, bounty_xlm=Decimal(bounty),
            mode=mode, token=token, deadline_seconds=deadline,
        )
    except Exception as e:  # noqa: BLE001
        typer.echo(f"❌ post failed: {e}")
        raise typer.Exit(code=1)
    typer.echo(f"✓ Posted job #{job_id} (bounty {bounty} XLM, mode {mode}).")


@job_app.command("list")
def list_jobs(
    status: str = typer.Option(None, "--status", help="Filter: open | claimed | submitted | done | cancelled"),
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
    board: str = typer.Option(None, "--board", help="JobBoard contract id override"),
):
    """List jobs (read-only, no wallet)."""
    client = _client(network, DEFAULT_WALLET_PATH, board, signing=False)
    jobs = client.list_open_jobs(status=status)
    if not jobs:
        typer.echo("No jobs found." if status is None else f"No jobs with status '{status}'.")
        return
    for j in jobs:
        bounty_xlm = j["bounty_stroops"] / 10_000_000
        typer.echo(
            f"  #{j['job_id']:>3}  [{j['status']:<9}] {j['mode']:<6} "
            f"{bounty_xlm:>10.4f} XLM  poster={j['poster'][:8]}…  escrow={(j['escrow'] or '')[:8]}…"
        )


@job_app.command("claim")
def claim(
    job_id: int = typer.Argument(..., help="Job id to claim"),
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
    board: str = typer.Option(None, "--board", help="JobBoard contract id override"),
):
    """Single-agent self-claim of an open job."""
    client = _client(network, wallet, board, signing=True)
    client.claim_job(job_id)
    typer.echo(f"✓ Claimed job #{job_id}.")


@job_app.command("assign")
def assign(
    job_id: int = typer.Argument(..., help="Job id to assign"),
    agent: str = typer.Option(..., "--agent", help="Agent unique name (Hive Registry) or G/C address"),
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
    board: str = typer.Option(None, "--board", help="JobBoard contract id override"),
    registry: str = typer.Option(None, "--registry", help="Hive Registry id override (for name resolution)"),
):
    """Poster-side: assign a specific agent (by name or address) to an open job."""
    client = _client(network, wallet, board, signing=True)
    agent_addr = _resolve_agent_address(client.context, agent, registry)
    client.assign_agent(job_id, agent_addr)
    typer.echo(f"✓ Assigned {agent} ({agent_addr[:8]}…) to job #{job_id}.")


@job_app.command("join")
def join(
    job_id: int = typer.Argument(..., help="Job id to join"),
    capability: str = typer.Option(..., "--capability", help="Capability tag you bring to the swarm"),
    share: int = typer.Option(..., "--share", help="Agreed bounty share in basis points (sum to 10000)"),
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
    board: str = typer.Option(None, "--board", help="JobBoard contract id override"),
):
    """Join a swarm job with an agreed bounty share."""
    if not 0 < share <= 10000:
        typer.echo(f"Error: --share must be between 1 and 10000 basis points (got {share}).")
        raise typer.Exit(code=1)
    client = _client(network, wallet, board, signing=True)
    client.join_swarm(job_id, capability, share)
    typer.echo(f"✓ Joined swarm for job #{job_id} ({share} bps, capability '{capability}').")


@job_app.command("submit")
def submit(
    job_id: int = typer.Argument(..., help="Job id"),
    proof: str = typer.Option(..., "--proof", help="Proof file path or string (must SHA-256 to the spec hash)"),
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
    board: str = typer.Option(None, "--board", help="JobBoard contract id override"),
):
    """Submit the completion proof for a job."""
    client = _client(network, wallet, board, signing=True)
    client.submit_proof(job_id, _proof_bytes(proof))
    typer.echo(f"✓ Submitted proof for job #{job_id}.")


@job_app.command("finalize")
def finalize(
    job_id: int = typer.Argument(..., help="Job id"),
    proof: str = typer.Option(..., "--proof", help="Proof file path or string (must SHA-256 to the spec hash)"),
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
    board: str = typer.Option(None, "--board", help="JobBoard contract id override"),
):
    """Release + split the bounty and mark the job done."""
    client = _client(network, wallet, board, signing=True)
    client.finalize(job_id, _proof_bytes(proof))
    typer.echo(f"✓ Finalized job #{job_id} — bounty released.")


@job_app.command("status")
def status(
    job_id: int = typer.Argument(..., help="Job id"),
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
    board: str = typer.Option(None, "--board", help="JobBoard contract id override"),
):
    """Show a job's claimants, swarm shares, and escrow state (read-only)."""
    client = _client(network, DEFAULT_WALLET_PATH, board, signing=False)
    job = client.get_job(job_id)
    typer.echo(f"Job #{job_id}")
    typer.echo(f"  status   : {job['status']}")
    typer.echo(f"  mode     : {job['mode']}")
    typer.echo(f"  bounty   : {job['bounty_stroops'] / 10_000_000:.7f} XLM")
    typer.echo(f"  poster   : {job['poster']}")
    typer.echo(f"  escrow   : {job['escrow']}")
    typer.echo(f"  deadline : {job['deadline']} (unix)")
    if job["mode"] == "swarm":
        members = client.get_swarm(job_id)
        shares = client.get_shares(job_id)
        typer.echo("  swarm    :")
        for m, s in zip(members, shares):
            typer.echo(f"    - {m}  {s} bps")
    else:
        typer.echo(f"  agent    : {job['agent']}")


def _proof_bytes(proof: str) -> bytes:
    """A proof file's raw bytes, or the UTF-8 bytes of a literal string."""
    if os.path.isfile(proof):
        with open(proof, "rb") as f:
            return f.read()
    return proof.encode("utf-8")


def _resolve_agent_address(context, agent: str, registry: str | None) -> str:
    """Pass through a G/C address; otherwise resolve a Hive Registry unique name."""
    from stellar_sdk import StrKey

    if StrKey.is_valid_ed25519_public_key(agent) or StrKey.is_valid_contract(agent):
        return agent
    from mycelium_sdk import HiveClient

    entry = HiveClient(context, registry_address=registry).resolve_agent(agent)
    addr = entry.get("public_key")
    if not addr:
        typer.echo(f"Error: could not resolve agent '{agent}' to an address.")
        raise typer.Exit(code=1)
    return addr
