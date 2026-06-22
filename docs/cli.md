# Mycelium CLI Internal Architecture Guide

This document covers the codebase structure, command routing, and configuration management of the Mycelium CLI (`cli/mycelium_cli` package).

---

## 🗂️ CLI Package Layout

The package separates the console app wrapper from the command implementation files:

```
cli/mycelium_cli/
├── __init__.py           # Package entrypoint
├── main.py               # Typer app initialization and command bindings
├── config.py             # TOML configuration helper (mycelium.toml parser)
└── commands/             # Command business logic modules
    ├── agent.py          # mycelium agent
    ├── call.py           # mycelium call
    ├── check.py          # mycelium check
    ├── compile.py        # mycelium compile
    ├── deploy.py         # mycelium deploy
    ├── discover.py       # mycelium discover
    ├── doctor.py         # mycelium doctor
    ├── events.py         # mycelium events
    ├── fund.py           # mycelium fund
    ├── init.py           # mycelium init
    ├── newwallet.py      # mycelium newwallet
    ├── pay.py            # mycelium pay
    ├── register.py       # mycelium register
    ├── resolve.py        # mycelium resolve
    ├── run.py            # mycelium run
    ├── status.py         # mycelium status
    └── test.py           # mycelium test
```

---

## 🎛️ Command Routing & Typer Setup (`main.py`)

The entry point of the CLI uses the `typer` framework. It initializes a root `Typer` instance and binds the execution commands to the modules inside the `commands/` directory:

```python
import typer
from mycelium_cli.commands import init, compile, deploy, status, newwallet

app = typer.Typer(
    name="mycelium",
    help="Mycelium: The Smart-Agent Platform for Stellar/Soroban.",
    no_args_is_help=True
)

@app.command("init")
def command_init(name: str = typer.Argument(..., help="Project directory name")):
    init.run_init(name)
```

This architecture keeps the entry point file small and decoupled, allowing individual command implementations to be easily developed and tested.

---

## ⚙️ Project Configuration (`config.py`)

Configuration management utilizes `tomlkit` to preserve comments and formatting within `mycelium.toml` when modifications are saved:

- **`load_config()`**: Resolves and parses the `mycelium.toml` file in the current working directory.
- **`get_value(section, key, default)`**: Safely accesses nested configuration keys (e.g., `onchain.contract_id`).
- **`set_value(section, key, value)`**: Writes configuration updates back to the file (e.g., writing the contract ID after deployment).

---

## 🏗️ Command Implementations

### `init.py` (Project Scaffolder)
Prompts the developer for framework, model, and agent name. It enforces regular expression checks (`^[a-zA-Z0-9_]{3,30}$`) and copies standard template scripts (`contract.py` containing a Counter contract, `agent.py` setting up an `AgentContext`) into the target directory.

### `deploy.py` (Contract Publisher)
Manages deployment sequence. It checks the network settings and asserts gas balances.
- Under the hood, the deploy command compiles the contract source and calls the official `stellar` binary via subprocess:
  ```bash
  stellar contract deploy --wasm <wasm_path> --source <wallet_seed> --rpc-url <rpc_url> --network-passphrase <passphrase>
  ```
- This ensures audited security signatures are used, bypassing manual XDR assembly.

### `status.py` (Health Dashboard)
Runs read-only ledger simulations to pull agent parameters (e.g., registry endpoint mappings and reputation score) without prompting the developer for passwords or spending transactions fees.

### `doctor.py` (Diagnostic Verifier)
Interrogates the local computer environment:
- Runs `stellar --version` to check CLI installations.
- Connects to RPC endpoints defined in `constants.py` to check latency.
- Validates Cargo/Rust targets (`wasm32-unknown-unknown` and `wasm32v1-none`).
