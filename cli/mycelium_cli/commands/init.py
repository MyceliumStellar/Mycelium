"""
`mycelium init` — scaffold a new agent project per sdk.md section 1.1:

    <project>/
    ├── mycelium.toml      # project / agent / onchain / registry config
    ├── agent.py           # outer LLM-orchestration logic
    ├── contract.py        # inner Soroban contract (Mycelium DSL)
    └── .mycelium/         # protected dir for the encrypted wallet (gitignored)
"""

import os
import re

import tomli_w

from mycelium_sdk.constants import HIVEMIND_REGISTRY_ADDRESS

UNIQUE_NAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,30}$")
VALID_FRAMEWORKS = ("langgraph", "gemini", "anthropic", "openai", "ollama", "custom")


def validate_unique_name(name: str) -> bool:
    return bool(UNIQUE_NAME_RE.match(name))


# ── file templates ───────────────────────────────────────────────────────────
_CONTRACT_TEMPLATE = '''"""Inner logic: a strictly-typed Mycelium → Soroban contract."""

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


# Frameworks that the one-call `run_agent_loop` helper drives directly.
_AGENT_LOOP_PROVIDERS = {"gemini", "anthropic"}


def _agent_loop_template(framework: str, model: str, unique_name: str, api_key_env: str) -> str:
    """A complete agent in one call — run_agent_loop wires the {framework} loop."""
    return f'''"""Outer logic: {framework}-orchestrated on-chain agent ({model})."""

import os

from mycelium import AgentContext, HiveClient, run_agent_loop, ContractTool

# Sovereign on-chain execution context (loads .mycelium/wallet.json).
context = AgentContext(keypair_path=".mycelium/wallet.json", network_type="testnet")
hive = HiveClient(context)

# Contract this agent is bound to (set by `mycelium run` / `mycelium agent --contract`).
CONTRACT_ID = os.environ.get("MYCELIUM_CONTRACT_ID", "")
# API key read from the environment (.env, gitignored, written by `mycelium init`).
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


def _agent_template(framework: str, model: str, unique_name: str) -> str:
    if framework in _AGENT_LOOP_PROVIDERS:
        return _agent_loop_template(
            framework, model, unique_name, _API_KEY_ENV.get(framework, "API_KEY")
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


def _build_config(project_name: str, framework: str, model: str, unique_name: str) -> dict:
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


# Maps an API framework to the env var its agent template reads the key from.
_API_KEY_ENV = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


def run_init(
    project_name: str,
    framework: str = "custom",
    model: str = "custom",
    unique_name: str | None = None,
    api_key: str | None = None,
) -> str:
    """
    Scaffold a project directory. Prompting for framework/model/unique_name is
    handled by the CLI layer; this function takes resolved values so it can be
    driven non-interactively (tests/CI). When `api_key` is given it is written
    to a gitignored `.env` so the agent can authenticate without re-prompting.
    Returns the project path.
    """
    if framework not in VALID_FRAMEWORKS:
        raise ValueError(f"framework must be one of {VALID_FRAMEWORKS}, got {framework!r}")
    unique_name = unique_name or project_name
    if not validate_unique_name(unique_name):
        raise ValueError(
            f"unique_name {unique_name!r} must match ^[a-zA-Z0-9_]{{3,30}}$"
        )

    os.makedirs(project_name, exist_ok=True)
    os.makedirs(os.path.join(project_name, ".mycelium"), exist_ok=True)

    with open(os.path.join(project_name, "mycelium.toml"), "wb") as f:
        tomli_w.dump(_build_config(project_name, framework, model, unique_name), f)
    with open(os.path.join(project_name, "contract.py"), "w") as f:
        f.write(_CONTRACT_TEMPLATE)
    with open(os.path.join(project_name, "agent.py"), "w") as f:
        f.write(_agent_template(framework, model, unique_name))
    with open(os.path.join(project_name, ".gitignore"), "w") as f:
        f.write(".mycelium/\nbuild/\n.env\n__pycache__/\n")

    if api_key:
        env_var = _API_KEY_ENV.get(framework, "API_KEY")
        env_path = os.path.join(project_name, ".env")
        with open(env_path, "w") as f:
            f.write(f"{env_var}={api_key}\n")
        try:
            os.chmod(env_path, 0o600)
        except OSError:
            pass

    return project_name
