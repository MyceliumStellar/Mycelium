"""
Google Gemini adapter (sdk.md §4.2).

Produces plain Python callables that Gemini's automatic function-calling can
invoke directly (Gemini reads the function signature + docstring), each backed
by a live on-chain call. Requires `google-generativeai` (install
`mycelium[gemini]`) only if you build a `GenerativeModel` from them — the helpers
themselves return ordinary functions.
"""

from typing import Any, Callable, List, Optional


def require_genai():
    """Import google.generativeai, with a clear error if the extra is missing."""
    try:
        import google.generativeai as genai
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ImportError(
            "The Gemini adapter needs google-generativeai. Install it with:\n"
            "    pip install 'mycelium[gemini]'"
        ) from exc
    return genai


def make_contract_function(
    context,
    function_name: str,
    contract_id: str,
    read_only: bool = False,
) -> Callable[..., str]:
    """
    Build a Gemini-callable function that invokes `function_name` on
    `contract_id`. The returned function takes a single `args` list and returns
    a human-readable result string for the model.
    """
    def contract_call(args: List[Any]) -> str:
        """Invoke an on-chain Soroban contract function and return the result."""
        try:
            result = context.call_contract(contract_id, function_name, list(args), read_only=read_only)
            return f"On-chain result: {getattr(result, 'return_value', result)}"
        except Exception as e:
            return f"Contract call failed: {e}"

    contract_call.__name__ = function_name
    return contract_call


def make_resolve_agent_function(hive_client) -> Callable[[str], str]:
    """A Gemini-callable function that resolves an agent via the Hive Registry."""
    def lookup_partner_agent(name_tag: str) -> str:
        """Query the Hive Registry to find a registered service agent by name."""
        try:
            meta = hive_client.resolve_agent(name_tag)
            return f"Agent found. Public Key: {meta['public_key']}, Endpoint: {meta['endpoint']}"
        except Exception:
            return "Agent name not registered in the Hive Registry."

    return lookup_partner_agent
