"""
`mycelium test` — dry-run the agent against a simulated ledger.

Runs the project's agent script exactly as `mycelium run` would, but with
dry-run mode forced on (MYCELIUM_DRY_RUN=1): every state-changing contract call
the agent makes is *simulated* (no signature, no fee, no on-chain mutation) and
recorded. Afterwards it prints a summary of every on-chain action the agent
would have taken, with the simulated return value and estimated fee — so you can
see what your agent does before it spends real lumens on testnet.

Read-only calls behave identically to a live run, so views still return real data.
"""

import os
import sys
from typing import Optional

from mycelium_cli.config import get_value
from mycelium_cli.commands.agent import run_agent


def _stroops_to_xlm(stroops: Optional[int]) -> str:
    if stroops is None:
        return "—"
    return f"{stroops / 1e7:.7f}"


def _print_summary() -> None:
    from mycelium_sdk import context as ctx_mod

    log = ctx_mod.DRY_RUN_LOG
    print("\n──────── dry-run summary ────────")
    if not log:
        print("  No state-changing contract calls were attempted.")
        print("  (Read-only calls run normally and are not listed here.)\n")
        return

    total_fee = 0
    for i, rec in enumerate(log, 1):
        args = ", ".join(map(repr, rec["args"]))
        print(f"  {i}. {rec['function']}({args})")
        print(f"     contract : {rec['contract_id']}")
        print(f"     returns  : {rec['sim_return']}")
        print(f"     est fee  : {_stroops_to_xlm(rec['est_fee_stroops'])} XLM")
        if rec["est_fee_stroops"]:
            total_fee += rec["est_fee_stroops"]
    print(f"\n  {len(log)} action(s) simulated · est. total fees {_stroops_to_xlm(total_fee)} XLM")
    print("  Nothing was signed or submitted. Run `mycelium run` to execute for real.\n")


def run_test(file: Optional[str] = None, contract: Optional[str] = None) -> list:
    from mycelium_sdk import context as ctx_mod

    file = file or get_value("agent", "script", "agent.py")
    contract = contract or get_value("onchain", "contract_id") or ""
    network = get_value("onchain", "network", "testnet")

    if not os.path.exists(file):
        print(f"Error: agent script {file} not found. Pass it explicitly or run from the project dir.")
        sys.exit(1)

    print(f"[test] Dry-running {file} ({network}) — state changes will be simulated only.\n")
    os.environ["MYCELIUM_DRY_RUN"] = "1"
    os.environ.setdefault("MYCELIUM_NETWORK", network)
    ctx_mod.reset_dry_run_log()

    try:
        run_agent(file, contract)
    finally:
        _print_summary()

    return list(ctx_mod.DRY_RUN_LOG)
