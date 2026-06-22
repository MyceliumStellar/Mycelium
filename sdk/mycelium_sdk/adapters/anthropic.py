"""
Anthropic (Claude) adapter.

Anthropic tool use is schema-driven: you pass JSON tool definitions to the
Messages API, then dispatch the model's `tool_use` blocks back to your code.
This adapter generates the tool schema for an on-chain contract call and a
dispatcher that executes the call. The `anthropic` SDK is only needed to run the
conversation loop, not to build the schema; install it with `mycelium[anthropic]`.
"""

from typing import Any, Callable, Dict, List, Optional


def require_anthropic():
    """Import the anthropic client, with a clear error if the extra is missing."""
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ImportError(
            "The Anthropic adapter needs the anthropic SDK. Install it with:\n"
            "    pip install 'mycelium[anthropic]'"
        ) from exc
    return anthropic


def contract_tool_schema(
    name: str,
    description: str,
    contract_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build an Anthropic tool definition for an on-chain contract call. The tool
    takes an `args` array; if `contract_id` is not fixed here, a `contract_id`
    string field is added to the schema.
    """
    properties: Dict[str, Any] = {
        "args": {
            "type": "array",
            "description": "Positional arguments for the contract function.",
            "items": {},
        }
    }
    required = ["args"]
    if contract_id is None:
        properties["contract_id"] = {
            "type": "string",
            "description": "The target Soroban contract id.",
        }
        required.append("contract_id")

    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": properties, "required": required},
    }


def make_tool_dispatcher(
    context,
    function_name: str,
    contract_id: Optional[str] = None,
    read_only: bool = False,
) -> Callable[[Dict[str, Any]], str]:
    """
    Build a dispatcher that executes a Claude `tool_use` block's `input` dict by
    invoking `function_name` on the contract, returning a string for the
    tool_result.
    """
    def dispatch(tool_input: Dict[str, Any]) -> str:
        target = contract_id or tool_input.get("contract_id")
        if not target:
            return "Error: no contract_id provided."
        try:
            result = context.call_contract(
                target, function_name, list(tool_input.get("args", [])), read_only=read_only
            )
            return f"On-chain result: {getattr(result, 'return_value', result)}"
        except Exception as e:
            return f"Contract call failed: {e}"

    return dispatch
