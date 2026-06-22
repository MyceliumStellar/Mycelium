"""Outer logic: Gemini-orchestrated on-chain agent (gemini-2.5-flash).

The agent is given three on-chain tools backed by our SDK's AgentContext (each
call is a real, signed Soroban transaction on testnet) and a goal that requires
reasoning about which operations to combine — driving the on-chain accumulator
to an exact target, then verifying the result on-chain.
"""

import os

from mycelium import AgentContext, HiveClient, U64
from mycelium_sdk.adapters import gemini

# Sovereign on-chain execution context (loads .mycelium/wallet.json).
context = AgentContext(keypair_path=".mycelium/wallet.json", network_type="testnet")
hive = HiveClient(context)

CONTRACT_ID = os.environ.get("MYCELIUM_CONTRACT_ID", "")
API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

TARGET = 42


# ── on-chain tools (each is a live Soroban transaction via the SDK) ──────────
def add(amount: int) -> str:
    """Add `amount` to the on-chain counter. Returns the new on-chain value."""
    # The contract param is u64; wrap with the DSL's U64 type so the SDK
    # marshals the correct width (no stellar_sdk import needed).
    r = context.call_contract(CONTRACT_ID, "add", [U64(amount)])
    return f"counter is now {r.return_value}"


def increment() -> str:
    """Increase the on-chain counter by exactly 1. Returns the new value."""
    r = context.call_contract(CONTRACT_ID, "increment", [])
    return f"counter is now {r.return_value}"


def get_count() -> str:
    """Read the current on-chain counter value (no transaction, read-only)."""
    v = context.call_contract(CONTRACT_ID, "get_count", [], read_only=True)
    return f"counter currently equals {v}"


def main():
    print("Agent 'myc_6465185c' online as", context.keypair.public_key)
    print(f"Bound contract: {CONTRACT_ID}")
    genai = gemini.require_genai()
    genai.configure(api_key=API_KEY)

    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        tools=[add, increment, get_count],
        system_instruction=(
            "You are a sovereign on-chain agent with a wallet on the Stellar "
            "testnet and tools that execute real Soroban transactions. Think "
            "step by step and actually call the tools to change on-chain state."
        ),
    )
    chat = model.start_chat(enable_automatic_function_calling=True)
    response = chat.send_message(
        f"The on-chain counter currently reads 0. Drive it to EXACTLY {TARGET}. "
        f"Use the 'add' tool for the large jump and 'increment' for the final "
        f"single steps (do not use add for the last few). When you believe it is "
        f"{TARGET}, call get_count to verify on-chain, then report the final value "
        f"and the operations you performed."
    )
    print("\n===== AGENT REPORT =====")
    print(response.text)
    print("===== FINAL ON-CHAIN STATE =====")
    print(get_count())


if __name__ == "__main__":
    main()
