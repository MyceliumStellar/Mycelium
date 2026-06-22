"""
`mycelium agent` — run a Mycelium agent runtime script.

Loads the developer's agent script (e.g. the scaffolded `agent.py`) as a module
and runs it, binding the on-chain contract id into the environment as
`MYCELIUM_CONTRACT_ID` so the script (and any `AgentContext` it builds) can read
it. If the script exposes a `main()` callable it is invoked; otherwise importing
the module is treated as running it (top-level code executes on import).
"""

import importlib.util
import os
import sys


def _load_dotenv(path: str) -> None:
    """Minimal .env loader: sets KEY=VALUE pairs into os.environ if not already set."""
    if not os.path.exists(path):
        return
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            value = value.strip()
            # Strip a single pair of matching surrounding quotes.
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            os.environ.setdefault(key, value)


def run_agent(file_path: str, contract_id: str):
    if not os.path.exists(file_path):
        print(f"Error: agent runtime file {file_path} not found.")
        sys.exit(1)

    print(f"[Agent] Loading runtime script: {file_path}")
    print(f"[Agent] Bound to on-chain contract: {contract_id}")

    # Expose the bound contract id to the agent script and any SDK it constructs.
    os.environ["MYCELIUM_CONTRACT_ID"] = contract_id

    # Load a sibling .env (written by `mycelium init` with the provider API key)
    # so the agent can authenticate without the key being hard-coded in source.
    _load_dotenv(os.path.join(os.path.dirname(os.path.abspath(file_path)), ".env"))

    module_name = os.path.splitext(os.path.basename(file_path))[0]
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        print(f"Error: could not load {file_path} as a Python module.")
        sys.exit(1)
    module = importlib.util.module_from_spec(spec)
    # Make the script's own directory importable (so it can import sibling files).
    sys.path.insert(0, os.path.dirname(os.path.abspath(file_path)) or ".")

    try:
        spec.loader.exec_module(module)
        if hasattr(module, "main") and callable(module.main):
            print("[Agent] Running main()...")
            module.main()
        print("[Agent] Runtime finished.")
    except KeyboardInterrupt:
        print("\n[Agent] Execution halted by user request.")
    except Exception as e:
        print(f"❌ Agent runtime error: {e}")
        sys.exit(1)
