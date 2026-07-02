"""
`mycelium init` — scaffold a new agent project per sdk.md section 1.1:

    <project>/
    ├── mycelium.toml      # project / agent / onchain / registry config
    ├── agent.py           # outer LLM-orchestration logic
    ├── contract.py        # inner Soroban contract (Mycelium DSL)
    └── .mycelium/         # protected dir for the encrypted wallet (gitignored)

The file templates live in `mycelium_sdk.scaffold` (shared with the IDE backend's
in-IDE agent creation so the two scaffolders never drift).
"""

import os

import tomli_w

from mycelium_sdk.scaffold import (
    CONTRACT_TEMPLATE,
    GITIGNORE,
    VALID_FRAMEWORKS,
    API_KEY_ENV as _API_KEY_ENV,
    agent_template,
    build_config,
    validate_unique_name,
)


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
        tomli_w.dump(build_config(project_name, framework, model, unique_name), f)
    with open(os.path.join(project_name, "contract.py"), "w", encoding="utf-8") as f:
        f.write(CONTRACT_TEMPLATE)
    with open(os.path.join(project_name, "agent.py"), "w", encoding="utf-8") as f:
        f.write(agent_template(framework, model, unique_name))
    with open(os.path.join(project_name, ".gitignore"), "w", encoding="utf-8") as f:
        f.write(GITIGNORE)

    if api_key:
        env_var = _API_KEY_ENV.get(framework, "API_KEY")
        env_path = os.path.join(project_name, ".env")
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(f"{env_var}={api_key}\n")
        try:
            os.chmod(env_path, 0o600)
        except OSError:
            pass

    return project_name
