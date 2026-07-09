"""
`mycelium memory …` — persistent agent memory: a big mutable off-chain store
with a tiny on-chain commitment (root, uri, version) per agent.

Where the deal/job commands move money, memory moves *state*: an agent writes
durable memories off-chain (free, fast), then `anchor`s a hash of that memory
on-chain at checkpoints. On a fresh machine `rehydrate` reads the anchor, fetches
the blob, re-hashes it, and refuses to load if it doesn't match — so memory is
portable AND tamper-evident without putting the data itself on-chain.

  remember   write a memory off-chain (no tx)
  recall     semantic-ish search over off-chain memory (no tx)
  anchor     publish the blob (optional) + commit its root on-chain (1 tx)
  verify     recompute the local root and compare to the on-chain anchor
  rehydrate  on a fresh machine: read anchor → fetch blob → verify → load
  status     show local count + on-chain version/root/uri

Backend defaults to the offline local store; `--backend supermemory` uses the
managed cloud (needs SUPERMEMORY_API_KEY). The anchor contract + network default
from mycelium.toml, mirroring `deploy` / `register` / `job` / `deal`.
"""

import os
from typing import List, Optional

import typer
from mycelium_cli.commands import resolve_network

from mycelium_cli.config import get_value

DEFAULT_WALLET_PATH = os.path.join(".mycelium", "wallet.json")
PASSPHRASE_ENV_VAR = "MYCELIUM_DECRYPT_KEY"

memory_app = typer.Typer(help="Persistent, portable, verifiable agent memory (off-chain + on-chain anchor).")


def _resolve_passphrase(label: str = "Wallet passphrase") -> str:
    """MYCELIUM_DECRYPT_KEY if set, else prompt — matches the rest of the CLI."""
    env_value = os.environ.get(PASSPHRASE_ENV_VAR)
    if env_value:
        return env_value
    return typer.prompt(label, hide_input=True)


def _context(network: Optional[str], wallet: str, *, signing: bool):
    """Build an AgentContext. Read-only commands skip wallet + passphrase."""
    from mycelium_sdk import AgentContext

    network = network or get_value("onchain", "network", "testnet")
    if signing:
        if not os.path.exists(wallet):
            typer.echo(f"Error: wallet {wallet} not found. Run `mycelium newwallet` first.")
            raise typer.Exit(code=1)
        return AgentContext(keypair_path=wallet, network_type=network, passphrase=_resolve_passphrase())
    return AgentContext.read_only(network_type=network)


def _memory(context, backend: str, anchor: Optional[str]):
    from mycelium_sdk import AgentMemory

    anchor = anchor or get_value("memory", "anchor_address", None)
    return AgentMemory(context, backend=backend, anchor_address=anchor)


def _backend_owner(network: Optional[str], wallet: str, backend: str, anchor: Optional[str], *, signing: bool):
    """Common setup: context + AgentMemory, surfacing a clean error if the cloud key is missing."""
    context = _context(network, wallet, signing=signing)
    try:
        mem = _memory(context, backend, anchor)
    except RuntimeError as exc:  # e.g. SupermemoryBackend without an API key
        typer.echo(f"Error: {exc}")
        raise typer.Exit(code=1)
    return mem


@memory_app.command("remember")
def remember(
    content: str = typer.Argument(..., help="The memory text to store"),
    tags: List[str] = typer.Option(None, "--tag", "-t", help="Tag(s) for the memory (repeatable)"),
    backend: str = typer.Option("local", "--backend", help="local or supermemory"),
    anchor: str = typer.Option(None, "--anchor", help="MemoryAnchor contract id override"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path (identifies the memory owner)"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Store a memory off-chain (no transaction)."""
    mem = _backend_owner(network, wallet, backend, anchor, signing=True)
    rid = mem.remember(content, list(tags) if tags else None)
    typer.echo(f"✓ Remembered (id={rid}). Off-chain only — run `mycelium memory anchor` to checkpoint on-chain.")


@memory_app.command("recall")
def recall(
    query: str = typer.Argument(..., help="What to search memory for"),
    k: int = typer.Option(5, "-k", help="Number of results"),
    backend: str = typer.Option("local", "--backend", help="local or supermemory"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path (identifies the memory owner)"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Search off-chain memory (no transaction)."""
    mem = _backend_owner(network, wallet, backend, None, signing=True)
    hits = mem.recall(query, k=k)
    if not hits:
        typer.echo("(no memories matched)")
        return
    for i, h in enumerate(hits, 1):
        tags = f"  [{', '.join(h.get('tags') or [])}]" if h.get("tags") else ""
        score = h.get("score")
        score_s = f"  ({score:.3f})" if isinstance(score, (int, float)) else ""
        typer.echo(f"  {i}.{score_s} {h['content']}{tags}")


@memory_app.command("anchor")
def anchor(
    uri: str = typer.Option("", "--uri", help="Where the blob is fetchable (https / file:// / supermemory://...)"),
    publish: str = typer.Option(None, "--publish", help="Write the canonical blob to this file and anchor file://<path>"),
    backend: str = typer.Option("local", "--backend", help="local or supermemory"),
    anchor: str = typer.Option(None, "--anchor", help="MemoryAnchor contract id override"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Checkpoint: commit the memory root (+uri) on-chain, bumping the version."""
    mem = _backend_owner(network, wallet, backend, anchor, signing=True)

    publish_fn = None
    if publish:
        def publish_fn(blob: bytes) -> str:
            path = os.path.abspath(publish)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "wb") as f:
                f.write(blob)
            return f"file://{path}"

    typer.echo("[memory] Computing root + anchoring on-chain...")
    try:
        version = mem.anchor(uri=uri, publish=publish_fn)
    except Exception as exc:
        typer.echo(f"Error anchoring: {exc}")
        raise typer.Exit(code=1)
    root = mem.memory_root().hex()
    typer.echo(f"✓ Anchored version {version}")
    typer.echo(f"  root: {root}")
    if mem.get_anchor():
        typer.echo(f"  uri:  {mem.get_anchor().get('uri') or '(none)'}")


@memory_app.command("verify")
def verify(
    backend: str = typer.Option("local", "--backend", help="local or supermemory"),
    anchor: str = typer.Option(None, "--anchor", help="MemoryAnchor contract id override"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Recompute the local root and compare to the on-chain anchor."""
    mem = _backend_owner(network, wallet, backend, anchor, signing=True)
    onchain = mem.get_anchor()
    if not onchain:
        typer.echo("✗ No on-chain anchor for this agent yet — run `mycelium memory anchor`.")
        raise typer.Exit(code=1)
    if mem.verify():
        typer.echo(f"✓ Verified: local memory matches on-chain anchor (version {onchain['version']}).")
    else:
        typer.echo("✗ MISMATCH: local memory differs from the on-chain anchor.")
        typer.echo(f"  local root:   {mem.memory_root().hex()}")
        typer.echo(f"  on-chain root:{onchain['root'].hex()}")
        raise typer.Exit(code=1)


@memory_app.command("rehydrate")
def rehydrate(
    backend: str = typer.Option("local", "--backend", help="local or supermemory"),
    anchor: str = typer.Option(None, "--anchor", help="MemoryAnchor contract id override"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """On a fresh machine: read the anchor, fetch the blob, verify the root, and load."""
    mem = _backend_owner(network, wallet, backend, anchor, signing=True)
    try:
        out = mem.rehydrate()
    except Exception as exc:
        typer.echo(f"✗ Rehydrate failed: {exc}")
        raise typer.Exit(code=1)
    typer.echo(f"✓ Rehydrated {out['records']} record(s) from on-chain anchor version {out['version']}.")


@memory_app.command("status")
def status(
    backend: str = typer.Option("local", "--backend", help="local or supermemory"),
    anchor: str = typer.Option(None, "--anchor", help="MemoryAnchor contract id override"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Show local memory count and the on-chain anchor (version/root/uri)."""
    mem = _backend_owner(network, wallet, backend, anchor, signing=True)
    typer.echo(f"owner:    {mem.owner}")
    typer.echo(f"backend:  {getattr(mem.backend, 'name', backend)}")
    try:
        typer.echo(f"local:    {mem.backend.count()} record(s)")
    except Exception:
        typer.echo("local:    (count unavailable)")
    onchain = mem.get_anchor()
    if not onchain:
        typer.echo("on-chain: not anchored yet")
        return
    typer.echo(f"on-chain: version {onchain['version']}")
    typer.echo(f"  root: {onchain['root'].hex()}")
    typer.echo(f"  uri:  {onchain.get('uri') or '(none)'}")
    typer.echo(f"  sync: {'in sync ✓' if mem.verify() else 'LOCAL AHEAD/DIVERGED ✗'}")
