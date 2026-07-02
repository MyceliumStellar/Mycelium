"""Mycelium CLI entry point (Typer)."""

import os

import typer

from mycelium_sdk.banner import show_startup_banner
from mycelium_cli.commands.init import run_init, validate_unique_name, VALID_FRAMEWORKS
from mycelium_cli.commands.newwallet import run_newwallet, DEFAULT_WALLET_PATH
from mycelium_cli.commands.compile import run_compile
from mycelium_cli.commands.deploy import run_deploy
from mycelium_cli.commands.register import run_register
from mycelium_cli.commands.check import run_check
from mycelium_cli.commands.agent import run_agent
from mycelium_cli.commands.discover import run_discover
from mycelium_cli.commands.resolve import run_resolve
from mycelium_cli.commands.status import run_status
from mycelium_cli.commands.fund import run_fund
from mycelium_cli.commands.call import run_call
from mycelium_cli.commands.pay import run_pay
from mycelium_cli.commands.doctor import run_doctor
from mycelium_cli.commands.events import run_events
from mycelium_cli.commands.run import run_run
from mycelium_cli.commands.test import run_test
from mycelium_cli.commands.jobs import job_app
from mycelium_cli.commands.deal import deal_app
from mycelium_cli.commands.memory import memory_app
from mycelium_cli.commands.verifier import verifier_app

app = typer.Typer(help="Mycelium Developer Framework CLI")
# Sovereign Job Boards: `mycelium job post|list|claim|assign|join|submit|finalize|status`
app.add_typer(job_app, name="job")
# A2A commerce (conditional x402 escrow between two agents):
# `mycelium deal open|release|refund|status`
app.add_typer(deal_app, name="deal")
# Persistent agent memory (off-chain store + tiny on-chain anchor):
# `mycelium memory remember|recall|anchor|verify|rehydrate|status`
app.add_typer(memory_app, name="memory")
# Staked judge pool (P2 trustless verification):
# `mycelium verifier register|stake|info|eligible|slash|accuracy`
app.add_typer(verifier_app, name="verifier")

PASSPHRASE_ENV_VAR = "MYCELIUM_DECRYPT_KEY"


__version__ = "0.4.2"


def _version_callback(value: bool):
    if value:
        typer.echo(f"mycelium {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", "-V", help="Show the Mycelium version and exit.",
        callback=_version_callback, is_eager=True,
    ),
):
    """Mycelium Developer Framework CLI."""
    show_startup_banner()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


def _resolve_passphrase(label: str, confirm: bool = False) -> str:
    """Use MYCELIUM_DECRYPT_KEY if set, otherwise prompt interactively."""
    env_value = os.environ.get(PASSPHRASE_ENV_VAR)
    if env_value:
        return env_value
    return typer.prompt(label, hide_input=True, confirmation_prompt=confirm)


def _select_model(framework: str) -> tuple[str, str | None]:
    """
    Resolve the target model for `framework`, returning (model, api_key).

    For API-backed frameworks we NEVER let the developer free-type a model name
    (a hallucinated id fails at runtime). Instead we take their API key, query
    the provider's live model catalogue, and have them pick from the real list.
    If discovery fails (bad key / offline) we fall back to manual entry so the
    wizard never hard-blocks. `api_key` is None for non-API frameworks.
    """
    from mycelium_sdk import models as model_discovery

    if not model_discovery.supports_discovery(framework):
        return typer.prompt("Target model", default="custom"), None

    # Cloud providers need a key; local runtimes (ollama) need a base URL instead.
    api_key = None
    base_url = None
    if model_discovery.requires_api_key(framework):
        api_key = typer.prompt(f"{framework.capitalize()} API key", hide_input=True).strip()
    else:
        base_url = typer.prompt(
            f"{framework.capitalize()} server URL",
            default=model_discovery.DEFAULT_OLLAMA_URL,
        ).strip()

    typer.echo(f"  Fetching available {framework} models...")
    try:
        available = model_discovery.list_models(framework, api_key, base_url=base_url)
    except model_discovery.ModelDiscoveryError as exc:
        typer.echo(f"  ⚠ Could not list models ({exc}).")
        return typer.prompt("Enter the model id manually"), api_key

    typer.echo(f"  {len(available)} models available:")
    for i, name in enumerate(available, 1):
        typer.echo(f"    [{i}] {name}")
    while True:
        choice = typer.prompt("Select a model by number")
        try:
            idx = int(choice)
            if 1 <= idx <= len(available):
                return available[idx - 1], api_key
        except ValueError:
            pass
        typer.echo(f"  Enter a number between 1 and {len(available)}.")


@app.command()
def init(
    project_name: str = typer.Argument(..., help="Name of the new project"),
    non_interactive: bool = typer.Option(
        False, "--yes", "--non-interactive", help="Skip prompts, use defaults"
    ),
):
    """Initialize a new Mycelium agent project."""
    framework, model, unique_name, api_key = "custom", "custom", project_name, None

    if not non_interactive:
        framework = typer.prompt(f"AI framework {list(VALID_FRAMEWORKS)}", default="custom")
        while framework not in VALID_FRAMEWORKS:
            typer.echo(f"  Must be one of {list(VALID_FRAMEWORKS)}.")
            framework = typer.prompt("AI framework", default="custom")
        model, api_key = _select_model(framework)
        unique_name = typer.prompt("Unique name (^[a-zA-Z0-9_]{3,30}$)", default=project_name)
        while not validate_unique_name(unique_name):
            typer.echo("  Invalid: 3-30 chars, alphanumeric/underscore only.")
            unique_name = typer.prompt("Unique name", default=project_name)

    path = run_init(
        project_name, framework=framework, model=model, unique_name=unique_name, api_key=api_key
    )
    typer.echo(f"✓ Project '{path}' initialized.")
    typer.echo("  Next: cd into it, run `mycelium newwallet`, then `mycelium compile`.")


@app.command()
def newwallet(
    path: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet output path"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing wallet"),
):
    """Generate an encrypted Ed25519 wallet."""
    passphrase = _resolve_passphrase("Encryption passphrase", confirm=True)
    public_key = run_newwallet(path=path, passphrase=passphrase, force=force)
    typer.echo(f"✓ Wallet created at {path}")
    typer.echo(f"  Public key: {public_key}")


@app.command()
def compile(
    file: str = typer.Argument(None, help="Contract file (defaults to mycelium.toml)"),
    output: str = typer.Option(None, "-o", "--output", help="Output WASM path"),
    optimize: bool = typer.Option(False, "--optimize", help="Size-optimize the WASM"),
    remote: bool = typer.Option(False, "--remote", help="Compile via the hosted backend (no local toolchain needed)"),
    local: bool = typer.Option(False, "--local", help="Force the local Rust/stellar-cli toolchain"),
):
    """Compile a Python contract to Soroban WASM.

    By default compiles locally if a Rust + stellar-cli toolchain is detected,
    otherwise compiles remotely via the hosted backend (zero local install).
    """
    run_compile(file, output, optimize=optimize, remote=remote or None, local=local)


@app.command()
def check(file: str = typer.Argument(..., help="Python contract file to validate")):
    """Validate a contract's AST and types without compiling."""
    run_check(file)


@app.command()
def deploy(
    network: str = typer.Option("testnet", help="testnet or mainnet"),
    wasm: str = typer.Option(None, help="WASM path (defaults to mycelium.toml)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
):
    """Deploy the compiled contract to Stellar/Soroban."""
    passphrase = _resolve_passphrase("Wallet passphrase")
    run_deploy(network=network, wasm_path=wasm, wallet_path=wallet, passphrase=passphrase)


@app.command()
def register(
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
):
    """Register the agent's unique name on the Hive Registry."""
    passphrase = _resolve_passphrase("Wallet passphrase")
    run_register(network=network, wallet_path=wallet, passphrase=passphrase)


@app.command()
def agent(
    file: str = typer.Argument(..., help="Agent runtime script"),
    contract: str = typer.Option(..., "--contract", help="On-chain contract id to bind"),
):
    """Start a Mycelium agent runtime."""
    run_agent(file, contract)


@app.command()
def agents(
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
    registry: str = typer.Option(None, "--registry", help="Hive Registry contract id override"),
    start_ledger: int = typer.Option(
        None, "--start-ledger", help="First ledger to scan (defaults to the RPC retention horizon)"
    ),
    no_resolve: bool = typer.Option(
        False, "--no-resolve", help="Skip per-agent resolution (faster; names + addresses only)"
    ),
):
    """Discover every agent registered on the Hive Registry (read-only, no wallet)."""
    run_discover(
        network=network, registry=registry, start_ledger=start_ledger, resolve=not no_resolve
    )


@app.command()
def resolve(
    name: str = typer.Argument(..., help="Unique agent name to look up"),
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
    registry: str = typer.Option(None, "--registry", help="Hive Registry contract id override"),
):
    """Resolve a single agent name to its Hive Registry entry (read-only, no wallet)."""
    run_resolve(name, network=network, registry=registry)


@app.command()
def status(
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
):
    """Show wallet, balance, network, deploy, and registry state in one screen."""
    run_status(network=network, wallet_path=wallet)


@app.command()
def fund(
    address: str = typer.Option(None, "--address", help="Address to fund (defaults to project wallet)"),
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
):
    """Top up a testnet wallet from Friendbot."""
    run_fund(address=address, network=network, wallet_path=wallet)


@app.command()
def call(
    function_name: str = typer.Argument(..., help="Contract function to invoke"),
    args: list[str] = typer.Argument(None, help="Positional arguments (ints/bools/addresses auto-typed)"),
    contract: str = typer.Option(None, "--contract", help="Contract id (defaults to mycelium.toml)"),
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
    send: bool = typer.Option(False, "--send", help="Sign & submit a state-changing tx (default: read-only)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path (only with --send)"),
):
    """Invoke a deployed contract function (read-only by default)."""
    passphrase = _resolve_passphrase("Wallet passphrase") if send else None
    run_call(
        function_name, args=args, contract=contract, network=network,
        send=send, wallet_path=wallet, passphrase=passphrase,
    )


@app.command()
def pay(
    recipient: str = typer.Argument(..., help="Registry name or G... address to pay"),
    amount: str = typer.Argument(..., help="Amount of XLM to send"),
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
    wallet: str = typer.Option(DEFAULT_WALLET_PATH, help="Wallet path"),
):
    """Send an XLM payment to a registry name or address (M2M settlement)."""
    passphrase = _resolve_passphrase("Wallet passphrase")
    run_pay(recipient, amount, network=network, wallet_path=wallet, passphrase=passphrase)


@app.command()
def doctor(
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
):
    """Verify connectivity (hosted compile + RPC; local toolchain optional) and print fixes."""
    run_doctor(network=network)


@app.command()
def events(
    contract: str = typer.Option(None, "--contract", help="Contract id (defaults to mycelium.toml)"),
    network: str = typer.Option(None, help="testnet or mainnet (defaults to mycelium.toml)"),
    start_ledger: int = typer.Option(None, "--start-ledger", help="First ledger to scan"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Stream new events until interrupted"),
):
    """Show (or stream with --follow) a contract's on-chain events."""
    run_events(contract=contract, network=network, start_ledger=start_ledger, follow=follow)


@app.command()
def run(
    file: str = typer.Argument(None, help="Agent script (defaults to agent.py)"),
    contract: str = typer.Option(None, "--contract", help="Contract id (defaults to mycelium.toml)"),
):
    """Run the project's agent, auto-reading contract id + network from mycelium.toml."""
    run_run(file=file, contract=contract)


@app.command()
def test(
    file: str = typer.Argument(None, help="Agent script (defaults to agent.py)"),
    contract: str = typer.Option(None, "--contract", help="Contract id (defaults to mycelium.toml)"),
):
    """Dry-run the agent: simulate every on-chain action without signing or spending."""
    run_test(file=file, contract=contract)


def main():
    app()


if __name__ == "__main__":
    main()
