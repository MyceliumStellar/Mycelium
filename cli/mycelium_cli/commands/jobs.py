"""
`mycelium job …` — drive the Sovereign Job Boards from the console.

A Typer sub-app (registered as the `job` command group in `main.py`) that thin-
wraps `mycelium_sdk.JobBoardClient`, reusing the same wallet load + passphrase
resolution as `deploy` / `register`. The JobBoard contract address defaults from
`mycelium.toml` (`[jobs].board_address`); override with `--board`.

Commands: post, list, claim, assign, join, submit, verdict, finalize, status.
"""

import hashlib
import os
import sys
from decimal import Decimal

import typer
from mycelium_cli.commands import resolve_network

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


def _parse_check(spec: str) -> dict:
    """Parse a --check 'id:weight:description' into a check dict (LLM-judged)."""
    parts = spec.split(":", 2)
    if len(parts) != 3:
        raise typer.BadParameter(f"--check must be 'id:weight:description' (got {spec!r}).")
    cid, weight, text = parts
    return {"id": cid.strip(), "weight": int(weight), "check": text.strip()}


@job_app.command("post")
def post(
    title: str = typer.Option(..., "--title", help="Job heading (required)"),
    description: str = typer.Option(..., "--description", help="What the work is (required)"),
    check: list[str] = typer.Option(..., "--check", help="A check 'id:weight:description' (repeatable, ≥1)"),
    judge_model: list[str] = typer.Option(..., "--judge-model", help="Judge model 'provider:model' (repeatable; the panel)"),
    bounty: str = typer.Option(..., "--bounty", help="Bounty in XLM"),
    judge: str = typer.Option(..., "--judge", help="Judge address (G…) — on-chain verdict authority that releases the escrow"),
    threshold: int = typer.Option(70, "--threshold", help="Pass score (0-100); payout only at/above this"),
    deliverable_type: str = typer.Option("any", "--type", help="Freeform deliverable type, e.g. text/sql, file/pptx"),
    mode: str = typer.Option("single", "--mode", help="single | swarm"),
    token: str = typer.Option(None, "--token", help="Payment token contract (defaults to native XLM SAC)"),
    deadline: int = typer.Option(86400, "--deadline", help="Refund deadline in seconds"),
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
    board: str = typer.Option(None, "--board", help="JobBoard contract id override"),
):
    """
    Post a self-describing bounty for ANY job. Title, description, checks, and the
    chosen judge panel are stored ON-CHAIN, so the bounty is fully readable from
    the contract. Example:

      mycelium job post --title "Promo script" --description "60s TigerGraph video" \\
        --check "hook:30:strong opening" --check "clarity:40:explains the bounty" \\
        --check "cta:30:clear call to action" \\
        --judge-model nvidia:deepseek-ai/deepseek-v4-pro \\
        --judge-model groq:llama-3.3-70b-versatile \\
        --bounty 5 --judge G... --threshold 70
    """
    client = _client(network, wallet, board, signing=True)
    try:
        checks = [_parse_check(c) for c in check]
        job_id = client.post_bounty(
            title=title, description=description, checks=checks, judge_models=judge_model,
            bounty_xlm=Decimal(bounty), judge=judge, pass_threshold=threshold,
            deliverable_type=deliverable_type, mode=mode, token=token, deadline_seconds=deadline,
        )
    except Exception as e:  # noqa: BLE001
        typer.echo(f"❌ post failed: {e}")
        raise typer.Exit(code=1)
    typer.echo(f"✓ Posted job #{job_id}: '{title}' (bounty {bounty} XLM, {len(checks)} checks, "
               f"{len(judge_model)}-model panel, threshold {threshold}).")


@job_app.command("list")
def list_jobs(
    status: str = typer.Option(None, "--status", help="Filter: open | claimed | submitted | done | cancelled"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True)"),
    board: str = typer.Option(None, "--board", help="JobBoard contract id override"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
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
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
    board: str = typer.Option(None, "--board", help="JobBoard contract id override"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Single-agent self-claim of an open job."""
    client = _client(network, wallet, board, signing=True)
    client.claim_job(job_id)
    typer.echo(f"✓ Claimed job #{job_id}.")


@job_app.command("assign")
def assign(
    job_id: int = typer.Argument(..., help="Job id to assign"),
    agent: str = typer.Option(..., "--agent", help="Agent unique name (Hive Registry) or G/C address"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
    board: str = typer.Option(None, "--board", help="JobBoard contract id override"),
    registry: str = typer.Option(None, "--registry", help="Hive Registry id override (for name resolution)"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
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
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
    board: str = typer.Option(None, "--board", help="JobBoard contract id override"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
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
    evidence: str = typer.Option(..., "--evidence", help="Deliverable file/string; its SHA-256 is anchored as the evidence_root"),
    uri: str = typer.Option("", "--uri", help="Optional pointer to the full bundle (recorded on-chain)"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Agent wallet path"),
    board: str = typer.Option(None, "--board", help="JobBoard contract id override"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Agent: anchor a pre-made deliverable's evidence on-chain (manual alternative to `do`)."""
    client = _client(network, wallet, board, signing=True)
    client.submit_evidence(job_id, _evidence_root(evidence), uri)
    typer.echo(f"✓ Submitted evidence for job #{job_id} — awaiting the judge panel.")


@job_app.command("do")
def do_job(
    job_id: int = typer.Argument(..., help="Job id to work on"),
    model: str = typer.Option(..., "--model", help="The agent's model 'provider:model' (nvidia/groq)"),
    claim: bool = typer.Option(True, "--claim/--no-claim", help="Self-claim the job first (single mode)"),
    no_revise: bool = typer.Option(False, "--no-revise", help="Single pass (skip the self-review pass)"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Agent wallet path"),
    board: str = typer.Option(None, "--board", help="JobBoard contract id override"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Agent: read the job from chain, do the actual work with your model, and submit real evidence."""
    client = _client(network, wallet, board, signing=True)
    try:
        bundle, content = client.execute_job(
            job_id, model, claim=claim, revise=not no_revise, evidence_uri="inline://deliverable")
    except Exception as e:  # noqa: BLE001
        typer.echo(f"❌ do failed: {e}")
        raise typer.Exit(code=1)
    typer.echo(f"✓ Job #{job_id}: produced {len(content.split())} words, evidence anchored "
               f"(root {bundle.evidence_root().hex()[:12]}…). Awaiting the panel.")


@job_app.command("judge")
def judge_job(
    job_id: int = typer.Argument(..., help="Job id to judge"),
    deliverable: str = typer.Option(..., "--deliverable", help="The deliverable text/file to score (what the agent produced)"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Judge wallet path"),
    board: str = typer.Option(None, "--board", help="JobBoard contract id override"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Judge: run the panel the JOB prescribes (from its on-chain spec) over the deliverable, record the score, and settle."""
    from mycelium_sdk.proof.evidence import EvidenceBundle, Artifact, Claim

    client = _client(network, wallet, board, signing=True)
    text = open(deliverable, encoding="utf-8").read() if os.path.isfile(deliverable) else deliverable
    job = client.get_job(job_id)
    # Rebuild the bundle the agent anchored is not needed for scoring text; the
    # on-chain evidence_root is what the verdict binds to.
    bundle = EvidenceBundle(job_id=job_id, rubric_hash=job.get("rubric_hash", ""),
                            artifacts=[Artifact.from_bytes("deliverable", "inline://deliverable", text.encode())],
                            claims=[])
    try:
        result = client.judge_and_settle(job_id, bundle, content_views={"inline://deliverable": text})
    except Exception as e:  # noqa: BLE001
        typer.echo(f"❌ judge failed: {e}")
        raise typer.Exit(code=1)
    verdict = "PASS ✅ — bounty released" if result.passed else "FAIL ❌ — no payout"
    typer.echo(f"Panel score {result.weighted_score:.1f} → {verdict}")
    for v in result.seat_verdicts:
        typer.echo("  " + v.model + ": " + ", ".join(f"{s.id}={s.score}" for s in v.scores))


@job_app.command("verdict")
def verdict(
    job_id: int = typer.Argument(..., help="Job id"),
    evidence: str = typer.Option(..., "--evidence", help="The same evidence bundle file/string the worker submitted"),
    passed: bool = typer.Option(None, "--pass/--fail", help="Whether the deliverable met the checks"),
    score: int = typer.Option(None, "--score", help="Numeric score 0-100 to record (defaults 100 on pass, 0 on fail)"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Judge wallet path"),
    board: str = typer.Option(None, "--board", help="JobBoard contract id override"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Judge: manually record a verdict (+score) and, on a pass, release the bounty. (Use `judge` for the LLM panel.)"""
    if passed is None:
        typer.echo("Error: specify --pass or --fail.")
        raise typer.Exit(code=1)
    client = _client(network, wallet, board, signing=True)
    sc = score if score is not None else (100 if passed else 0)
    client.settle(job_id, passed, sc, _evidence_root(evidence))
    if passed:
        typer.echo(f"✓ Verdict PASS (score {sc}) for job #{job_id} — bounty released to the worker.")
    else:
        typer.echo(f"✓ Verdict FAIL (score {sc}) for job #{job_id} — no payout; depositor may refund after the deadline.")


@job_app.command("finalize")
def finalize(
    job_id: int = typer.Argument(..., help="Job id"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Poster wallet path"),
    board: str = typer.Option(None, "--board", help="JobBoard contract id override"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Poster: close the record of a verified job (the bounty is already released)."""
    client = _client(network, wallet, board, signing=True)
    client.finalize(job_id)
    typer.echo(f"✓ Finalized job #{job_id} — record closed.")


@job_app.command("status")
def status(
    job_id: int = typer.Argument(..., help="Job id"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True)"),
    board: str = typer.Option(None, "--board", help="JobBoard contract id override"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Show a job's full on-chain detail — title, description, checks, chosen panel, score — plus escrow/claimants."""
    import json as _json

    client = _client(network, DEFAULT_WALLET_PATH, board, signing=False)
    job = client.get_job(job_id)
    typer.echo(f"Job #{job_id}: {job.get('title') or '(untitled)'}")
    if job.get("description"):
        typer.echo(f"  description : {job['description']}")
    spec = {}
    if job.get("spec"):
        try:
            spec = _json.loads(job["spec"])
        except Exception:
            pass
    if spec.get("criteria"):
        typer.echo("  checks      :")
        for c in spec["criteria"]:
            typer.echo(f"    - {c['id']} ({c['weight']}): {c['check']}")
        typer.echo(f"  panel       : {spec.get('judges', {}).get('models', [])}")
        typer.echo(f"  threshold   : {spec.get('pass_threshold')}")
    typer.echo(f"  status   : {job['status']}    score: {job.get('score')}")
    typer.echo(f"  mode     : {job['mode']}    bounty: {job['bounty_stroops'] / 10_000_000:.7f} XLM")
    typer.echo(f"  poster   : {job['poster']}")
    typer.echo(f"  judge    : {job.get('judge')}")
    typer.echo(f"  escrow   : {job['escrow']}")
    if job.get("evidence_uri"):
        typer.echo(f"  evidence : {job['evidence_uri']} (root {job.get('evidence_root','')[:12]}…)")
    typer.echo(f"  deadline : {job['deadline']} (unix)")
    if job["mode"] == "swarm":
        members = client.get_swarm(job_id)
        shares = client.get_shares(job_id)
        typer.echo("  swarm    :")
        for m, s in zip(members, shares):
            typer.echo(f"    - {m}  {s} bps")
    else:
        typer.echo(f"  agent    : {job['agent']}")


@job_app.command("models")
def models(
    provider: str = typer.Option("nvidia", "--provider", help="Provider to list models for: nvidia | groq"),
):
    """List the models a provider serves (for choosing a judge panel or an agent's model)."""
    from mycelium_sdk.proof import list_models

    try:
        ids = list_models(provider)
    except Exception as e:  # noqa: BLE001
        typer.echo(f"❌ could not list {provider} models: {e}")
        raise typer.Exit(code=1)
    typer.echo(f"{provider}: {len(ids)} models")
    for i in ids:
        typer.echo(f"  {provider}:{i}")


@job_app.command("critique")
def critique(
    job_id: int = typer.Argument(..., help="Job id to inspect critique for"),
):
    """View the judge panel's detailed markdown critique and scores for a judged job."""
    import json

    critique_path = os.path.join(".mycelium", "critiques", f"job_{job_id}_critique.json")
    if not os.path.exists(critique_path):
        typer.echo(f"❌ critique not found for Job #{job_id}. Run `mycelium job judge` first.")
        raise typer.Exit(code=1)

    try:
        with open(critique_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        typer.echo(data.get("critique_markdown", ""))
    except Exception as e:
        typer.echo(f"❌ failed to read critique report: {e}")
        raise typer.Exit(code=1)


def _evidence_root(evidence: str) -> bytes:
    """
    The 32-byte evidence_root for a submission. If `evidence` is a file, hash its
    bytes; otherwise hash the literal string. (The SDK's `proof.EvidenceBundle`
    produces the same kind of SHA-256 root over a structured bundle; the CLI keeps
    it simple by hashing whatever you point it at.)
    """
    if os.path.isfile(evidence):
        with open(evidence, "rb") as f:
            data = f.read()
    else:
        data = evidence.encode("utf-8")
    return hashlib.sha256(data).digest()


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
