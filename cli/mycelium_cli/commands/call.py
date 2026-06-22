"""
`mycelium call <fn> [args...]` — invoke a deployed contract function from the CLI.

By default the call is read-only (simulated, no signature, no fee) — perfect for
views and getters. Pass `--send` to sign and submit a state-changing transaction,
which loads the project wallet and prompts for its passphrase.

Argument widths are marshalled automatically from the contract spec (fetched
once from RPC), so `mycelium call add 40` "just works" — no U64() wrapper needed.
String tokens are interpreted as ints/bools/addresses where unambiguous and left
as strings otherwise.
"""

import sys
from typing import Any, List, Optional

from mycelium_cli.config import get_value

DEFAULT_WALLET_PATH = ".mycelium/wallet.json"


def _parse_arg(token: str) -> Any:
    """Best-effort coerce a CLI string token into a Python value.

    Integers and bools are recognised; addresses/symbols/strings are left as
    strings (AgentContext + the contract spec marshal them to the right SCVal).
    Prefix a value with `s:` to force it to stay a string (e.g. `s:42`).
    """
    if token.startswith("s:"):
        return token[2:]
    low = token.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(token)
    except ValueError:
        return token


def run_call(
    function_name: str,
    args: Optional[List[str]] = None,
    contract: Optional[str] = None,
    network: Optional[str] = None,
    send: bool = False,
    wallet_path: str = DEFAULT_WALLET_PATH,
    passphrase: Optional[str] = None,
) -> Any:
    from mycelium_sdk import AgentContext

    network = network or get_value("onchain", "network", "testnet")
    contract = contract or get_value("onchain", "contract_id")
    if not contract:
        print(
            "Error: no contract id. Pass --contract C..., or run inside a project "
            "whose mycelium.toml has [onchain].contract_id (after `mycelium deploy`)."
        )
        sys.exit(1)

    parsed = [_parse_arg(a) for a in (args or [])]

    if send:
        import os

        if not os.path.exists(wallet_path):
            print(f"Error: wallet {wallet_path} not found. Run `mycelium newwallet` first.")
            sys.exit(1)
        context = AgentContext(
            keypair_path=wallet_path, network_type=network, passphrase=passphrase
        )
    else:
        context = AgentContext.read_only(network_type=network)

    print(
        f"[call] {function_name}({', '.join(map(repr, parsed))}) on {contract} "
        f"({'send' if send else 'read-only'})..."
    )
    try:
        result = context.call_contract(
            contract, function_name, parsed, read_only=not send
        )
    except Exception as e:
        print(f"❌ Call failed: {e}")
        sys.exit(1)

    if send:
        # call_contract returns a TxResult for state-changing calls.
        tx_hash = getattr(result, "hash", None)
        ret = getattr(result, "return_value", result)
        print(f"✓ Submitted. Tx: {tx_hash}")
        print(f"  return: {ret}")
    else:
        print(f"✓ {result}")
    return result
