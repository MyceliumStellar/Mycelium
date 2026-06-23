"""
Shared agent-project scaffolding templates — the single source of truth used by
both `mycelium init` (CLI) and the IDE backend's `POST /api/agents/scaffold`
(in-IDE agent creation), so the two paths never drift.

Pure string/dict builders with no filesystem or network side effects; callers
decide where the bytes land (local disk for the CLI, a GitHub repo for the IDE).
"""

import re

from mycelium_sdk.constants import HIVEMIND_REGISTRY_ADDRESS

UNIQUE_NAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,30}$")
VALID_FRAMEWORKS = ("langgraph", "gemini", "anthropic", "openai", "ollama", "custom")

# Maps an API framework to the env var its agent template reads the key from.
API_KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}

# Frameworks that the one-call `run_agent_loop` helper drives directly.
_AGENT_LOOP_PROVIDERS = {"gemini", "anthropic"}

GITIGNORE = ".mycelium/\nbuild/\n.env\n__pycache__/\n"

CONTRACT_TEMPLATE = '''"""Inner logic: a strictly-typed Mycelium → Soroban contract."""

from mycelium import contract, external, view, Env, U64


@contract
class Counter:
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def increment(self) -> U64:
        count = self.storage.get("count", U64(0))
        count = count + U64(1)
        self.storage.set("count", count)
        return count

    @view
    def get_count(self) -> U64:
        return self.storage.get("count", U64(0))
'''


def validate_unique_name(name: str) -> bool:
    return bool(UNIQUE_NAME_RE.match(name))


def _agent_loop_template(framework: str, model: str, unique_name: str, api_key_env: str) -> str:
    return f'''"""Outer logic: {framework}-orchestrated on-chain agent ({model})."""

import os

from mycelium import AgentContext, HiveClient, run_agent_loop, ContractTool

# Sovereign on-chain execution context (loads .mycelium/wallet.json).
context = AgentContext(keypair_path=".mycelium/wallet.json", network_type="testnet")
hive = HiveClient(context)

# Contract this agent is bound to (set by `mycelium run` / `mycelium agent --contract`).
CONTRACT_ID = os.environ.get("MYCELIUM_CONTRACT_ID", "")
# API key read from the environment (.env, gitignored).
API_KEY = os.environ.get("{api_key_env}")


def main():
    print("Agent '{unique_name}' online as", context.keypair.public_key)
    answer = run_agent_loop(
        "You are an on-chain agent. Increment your counter contract, "
        "then report the new value.",
        context=context,
        provider="{framework}",
        model="{model}",
        api_key=API_KEY,
        contract_id=CONTRACT_ID,
        tools=[
            ContractTool("increment"),
            ContractTool("get_count", read_only=True),
        ],
        hive=hive,  # exposes a lookup_partner_agent tool for Hive Registry discovery
    )
    print(answer)


if __name__ == "__main__":
    main()
'''


def agent_template(framework: str, model: str, unique_name: str) -> str:
    """Return the agent.py source for `framework`/`model`."""
    if framework in _AGENT_LOOP_PROVIDERS:
        return _agent_loop_template(
            framework, model, unique_name, API_KEY_ENV.get(framework, "API_KEY")
        )
    return f'''"""Outer logic: AI orchestration ({framework} / {model})."""

from mycelium import AgentContext

# Sovereign on-chain execution context (loads .mycelium/wallet.json).
context = AgentContext(keypair_path=".mycelium/wallet.json", network_type="testnet")


def main():
    print("Agent '{unique_name}' online as", context.keypair.public_key)
    # TODO: wire your {framework} workflow here and expose contract calls
    # via context.call_contract(...).


if __name__ == "__main__":
    main()
'''


def readme_template(project_name: str, framework: str, model: str, unique_name: str) -> str:
    """A starter README for a scaffolded agent repo."""
    return f'''# {project_name}

A [Mycelium](https://github.com/) on-chain agent — `{unique_name}` ({framework} / {model}).

## Files
- `contract.py` — the inner Soroban smart contract (Mycelium DSL).
- `agent.py` — the outer {framework} orchestration logic.
- `mycelium.toml` — project / agent / on-chain / registry config.

## Build → Deploy → Register
```bash
mycelium compile          # contract.py -> build/contract.wasm (remote, no toolchain)
mycelium newwallet        # create an encrypted wallet
mycelium deploy           # pure-Python deploy to testnet
mycelium register         # publish to the Hive Registry
```

The encrypted `.mycelium/wallet.json` is safe at rest; the passphrase is never stored.
'''


def build_config(project_name: str, framework: str, model: str, unique_name: str) -> dict:
    """Return the mycelium.toml config dict for a new project."""
    return {
        "project": {"name": project_name, "version": "0.1.0", "author": "Developer"},
        "agent": {"framework": framework, "model": model, "unique_name": unique_name},
        "onchain": {
            "source_contract": "contract.py",
            "target_wasm": "build/contract.wasm",
            "network": "testnet",
            "contract_id": "",
            "wallet_public_key": "",
        },
        "registry": {
            "hive_registry_address": HIVEMIND_REGISTRY_ADDRESS,
            "service_endpoint": f"https://{unique_name}.agents.mycelium.sh/api/v1",
            "capabilities": [],
        },
    }


def config_to_toml(config: dict) -> str:
    """Serialize a config dict to a TOML string (for committing to a repo)."""
    import tomli_w

    return tomli_w.dumps(config)
