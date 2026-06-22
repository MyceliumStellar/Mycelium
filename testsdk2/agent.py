"""Outer logic: Gemini-orchestrated on-chain agent (gemini-2.5-flash)."""

import os

from mycelium import AgentContext, HiveClient
from mycelium_sdk.adapters import gemini

# Sovereign on-chain execution context (loads .mycelium/wallet.json).
context = AgentContext(keypair_path=".mycelium/wallet.json", network_type="testnet")
hive = HiveClient(context)

# Contract this agent is bound to (set by `mycelium agent --contract ...`).
CONTRACT_ID = os.environ.get("MYCELIUM_CONTRACT_ID", "")
# API key is read from the environment (.env, gitignored, written by `mycelium init`).
API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def main():
    print("Agent 'myc2_dd9246f1' online as", context.keypair.public_key)
    genai = gemini.require_genai()
    genai.configure(api_key=API_KEY)

    tools = [gemini.make_resolve_agent_function(hive)]
    if CONTRACT_ID:
        tools.append(gemini.make_contract_function(context, "increment", CONTRACT_ID))

    model = genai.GenerativeModel(model_name="gemini-2.5-flash", tools=tools)
    chat = model.start_chat(enable_automatic_function_calling=True)
    response = chat.send_message(
        "You are an on-chain agent. Increment your counter contract, "
        "then report the new value."
    )
    print(response.text)


if __name__ == "__main__":
    main()
