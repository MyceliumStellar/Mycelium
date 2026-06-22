"""
`mycelium run` — run the project's agent with config auto-wired.

A convenience alias for `mycelium agent`: instead of passing
`--contract C...` by hand, it reads `[onchain].contract_id` (and the agent
script, defaulting to agent.py) from mycelium.toml. The network is exported as
`MYCELIUM_NETWORK` so the runtime can pick it up.
"""

import os
import sys
from typing import Optional

from mycelium_cli.config import get_value
from mycelium_cli.commands.agent import run_agent


def run_run(file: Optional[str] = None, contract: Optional[str] = None) -> None:
    file = file or get_value("agent", "script", "agent.py")
    contract = contract or get_value("onchain", "contract_id") or ""
    network = get_value("onchain", "network", "testnet")

    if not os.path.exists(file):
        print(f"Error: agent script {file} not found. Pass it explicitly or run from the project dir.")
        sys.exit(1)
    if not contract:
        print(
            "Warning: no [onchain].contract_id in mycelium.toml — running unbound.\n"
            "  Deploy first with `mycelium deploy`, or pass --contract C..."
        )

    os.environ.setdefault("MYCELIUM_NETWORK", network)
    run_agent(file, contract)
