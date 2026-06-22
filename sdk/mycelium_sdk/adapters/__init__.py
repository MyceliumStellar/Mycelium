"""
AI orchestrator adapters (sdk.md §4).

These bridge the on-chain SDK (`AgentContext` / `HiveClient`) to popular LLM
frameworks by exposing contract calls as framework-native "tools". Each adapter
lives in its own module and imports its framework lazily, so the heavy optional
dependency is only required if you actually use that adapter:

    pip install "mycelium[langgraph]"   # or [gemini] / [anthropic]

Example:

    from mycelium_sdk import AgentContext
    from mycelium_sdk.adapters.langgraph import make_contract_tool

    ctx = AgentContext(".mycelium/wallet.json")
    trade = make_contract_tool(ctx, "execute_trade",
                               description="Execute an on-chain trade.")
"""

from mycelium_sdk.adapters import langgraph, gemini, anthropic

__all__ = ["langgraph", "gemini", "anthropic"]
