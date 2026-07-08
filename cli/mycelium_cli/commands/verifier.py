"""
`mycelium verifier …` — the staked judge pool (P2 trustless verification).

A judge registers its model capability and stakes XLM to become eligible to sit
on panels; the verification market slashes outliers and tracks accuracy (verifier
reputation). Thin wrapper over `mycelium_sdk.proof.VerifierRegistryClient`,
reusing the same wallet/passphrase flow as the rest of the CLI. The registry
address defaults from `mycelium.toml` (`[verifier].registry_address`).

Commands: register, stake, info, eligible, request-unstake, withdraw, slash, accuracy.
"""

import os
from decimal import Decimal

import typer
from mycelium_cli.commands import resolve_network

from mycelium_cli.config import get_value

DEFAULT_WALLET_PATH = os.path.join(".mycelium", "wallet.json")
PASSPHRASE_ENV_VAR = "MYCELIUM_DECRYPT_KEY"

verifier_app = typer.Typer(help="Staked judge pool: register, stake, slash (P2 trustless verification).")


def _passphrase() -> str:
    return os.environ.get(PASSPHRASE_ENV_VAR) or typer.prompt("Wallet passphrase", hide_input=True)


def _registry(override):
    addr = override or get_value("verifier", "registry_address")
    if not addr:
        typer.echo("Error: no VerifierRegistry address. Set [verifier].registry_address in "
                   "mycelium.toml or pass --registry.")
        raise typer.Exit(code=1)
    return addr


def _client(network, wallet, registry, *, signing):
    from mycelium_sdk import AgentContext
    from mycelium_sdk.proof import VerifierRegistryClient

    network = network or get_value("onchain", "network", "testnet")
    if signing:
        if not os.path.exists(wallet):
            typer.echo(f"Error: wallet {wallet} not found. Run `mycelium newwallet` first.")
            raise typer.Exit(code=1)
        ctx = AgentContext(keypair_path=wallet, network_type=network, passphrase=_passphrase())
    else:
        ctx = AgentContext.read_only(network_type=network)
    return VerifierRegistryClient(ctx, _registry(registry))


@verifier_app.command("register")
def register(
    tags: str = typer.Option(..., "--tags", help="Model families you run, e.g. 'nvidia:deepseek-ai/deepseek-v4-pro,groq:llama-3.3-70b-versatile'"),
    endpoint: str = typer.Option("", "--endpoint", help="Optional public endpoint"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True), wallet: str = typer.Option(DEFAULT_WALLET_PATH),
    registry: str = typer.Option(None, "--registry"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Announce judging capability."""
    _client(network, wallet, registry, signing=True).register(tags, endpoint)
    typer.echo("✓ Registered as a verifier.")


@verifier_app.command("stake")
def stake(
    amount: str = typer.Argument(..., help="XLM to bond"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True), wallet: str = typer.Option(DEFAULT_WALLET_PATH),
    registry: str = typer.Option(None, "--registry"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Lock an XLM bond (adds to existing stake)."""
    _client(network, wallet, registry, signing=True).stake(Decimal(amount))
    typer.echo(f"✓ Staked {amount} XLM.")


@verifier_app.command("info")
def info(
    judge: str = typer.Argument(..., help="Judge address (G…)"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True), registry: str = typer.Option(None, "--registry"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Show a judge's stake, model tags, and accuracy (read-only)."""
    c = _client(network, DEFAULT_WALLET_PATH, registry, signing=False)
    g = c.get(judge)
    typer.echo(f"Verifier {judge[:10]}…")
    typer.echo(f"  stake    : {g['stake_xlm']} XLM   active: {g['active']}   eligible: {c.is_eligible(judge)}")
    typer.echo(f"  tags     : {g['tags']}")
    typer.echo(f"  accuracy : {g['agreed']}/{g['jobs']} votes ({g['accuracy_bps']/100:.1f}%)")


@verifier_app.command("eligible")
def eligible(
    judge: str = typer.Argument(...), network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True),
    registry: str = typer.Option(None, "--registry"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Whether a judge is bonded enough to sit on panels."""
    typer.echo(_client(network, DEFAULT_WALLET_PATH, registry, signing=False).is_eligible(judge))


@verifier_app.command("request-unstake")
def request_unstake(
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True), wallet: str = typer.Option(DEFAULT_WALLET_PATH),
    registry: str = typer.Option(None, "--registry"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Begin the unbonding period before withdrawing your stake."""
    _client(network, wallet, registry, signing=True).request_unstake()
    typer.echo("✓ Unbonding started; withdraw after the delay.")


@verifier_app.command("withdraw")
def withdraw(
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True), wallet: str = typer.Option(DEFAULT_WALLET_PATH),
    registry: str = typer.Option(None, "--registry"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Reclaim your (possibly slashed) stake after unbonding."""
    _client(network, wallet, registry, signing=True).withdraw()
    typer.echo("✓ Withdrew stake.")


@verifier_app.command("slash")
def slash(
    judge: str = typer.Argument(...),
    amount: str = typer.Option(..., "--amount", help="XLM to slash"),
    reason: str = typer.Option("outlier", "--reason"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True), wallet: str = typer.Option(DEFAULT_WALLET_PATH),
    registry: str = typer.Option(None, "--registry"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Market only: cut a judge's stake (outlier/no-show verdict)."""
    _client(network, wallet, registry, signing=True).slash(judge, Decimal(amount), reason)
    typer.echo(f"✓ Slashed {amount} XLM from {judge[:10]}… ({reason}).")


@verifier_app.command("accuracy")
def accuracy(
    judge: str = typer.Argument(...),
    agreed: bool = typer.Option(..., "--agreed/--disagreed", help="Did the judge track the panel median?"),
    network: str = typer.Option(None, "--network", "-n", help="Network: testnet or mainnet (defaults to mycelium.toml)"), use_testnet: bool = typer.Option(False, "--testnet", "-t", help="Use Stellar testnet", is_flag=True), use_mainnet: bool = typer.Option(False, "--mainnet", "-m", help="Use Stellar mainnet", is_flag=True), wallet: str = typer.Option(DEFAULT_WALLET_PATH),
    registry: str = typer.Option(None, "--registry"),
):
    network = resolve_network(network, use_testnet, use_mainnet)
    """Market only: record whether a judge's verdict tracked the median (verifier reputation)."""
    _client(network, wallet, registry, signing=True).record_accuracy(judge, agreed)
    typer.echo(f"✓ Recorded accuracy ({'agreed' if agreed else 'disagreed'}) for {judge[:10]}….")
