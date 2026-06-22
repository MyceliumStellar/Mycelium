"""
LangGraph / LangChain adapter (sdk.md §4.1).

Turns an on-chain contract function into a LangChain `@tool` that a LangGraph
node can invoke. Requires `langchain-core` (install `mycelium[langgraph]`).
"""

from typing import Any, Callable, List, Optional


def _require_langchain():
    try:
        from langchain_core.tools import tool  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ImportError(
            "The LangGraph adapter needs langchain-core. Install it with:\n"
            "    pip install 'mycelium[langgraph]'"
        ) from exc
    return tool


def make_contract_tool(
    context,
    function_name: str,
    contract_id: Optional[str] = None,
    description: Optional[str] = None,
    name: Optional[str] = None,
    read_only: bool = False,
) -> Callable:
    """
    Build a LangChain tool that invokes `function_name` on a Soroban contract.

    The returned tool accepts a single `args` list (the contract call arguments).
    If `contract_id` is omitted, the tool's first argument is treated as the
    target contract id.
    """
    tool = _require_langchain()
    tool_name = name or function_name
    tool_doc = description or f"Invoke '{function_name}' on a Soroban contract."

    @tool(tool_name)
    def _contract_tool(args: List[Any]) -> str:
        target = contract_id
        call_args = list(args)
        if target is None:
            if not call_args:
                return "Error: no contract_id provided and none configured."
            target, call_args = call_args[0], call_args[1:]
        try:
            result = context.call_contract(target, function_name, call_args, read_only=read_only)
            tx_hash = getattr(result, "hash", None)
            return (
                f"Settled on-chain. Tx: {tx_hash}, return: {getattr(result, 'return_value', result)}"
                if tx_hash else f"Result: {result}"
            )
        except Exception as e:  # surface failures to the agent loop
            return f"Contract call failed: {e}"

    _contract_tool.__doc__ = tool_doc
    return _contract_tool


def make_resolve_agent_tool(hive_client, name: str = "lookup_partner_agent") -> Callable:
    """A LangChain tool that resolves an agent name via the Hive Registry."""
    tool = _require_langchain()

    @tool(name)
    def _resolve(name_tag: str) -> str:
        """Look up a registered agent by its unique name in the Hive Registry."""
        try:
            meta = hive_client.resolve_agent(name_tag)
            return f"Agent found. Public Key: {meta['public_key']}, Endpoint: {meta['endpoint']}"
        except Exception:
            return "Agent name not registered in the Hive Registry."

    return _resolve
