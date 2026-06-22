"""
One-call agent loop — collapse the agent.py boilerplate.

A scaffolded Mycelium agent today hand-wires the provider client, builds tools
from the contract + Hive Registry, runs the conversation loop, and dispatches
tool calls. `run_agent_loop` does all of that:

    from mycelium_sdk import AgentContext, HiveClient, run_agent_loop, ContractTool

    ctx = AgentContext(".mycelium/wallet.json")
    answer = run_agent_loop(
        "Increment the counter, then report the new value.",
        context=ctx,
        provider="anthropic",                 # or "gemini"
        contract_id=CONTRACT_ID,
        tools=[ContractTool("increment"), ContractTool("get_count", read_only=True)],
        hive=HiveClient(ctx),                 # optional: adds a resolve-agent tool
    )

It returns the model's final text. Contract calls go through the same
`AgentContext.call_contract` path as everything else (spec-marshalled args,
retry/backoff, dry-run), so a dry-run context makes the whole loop a simulation.

The provider SDK (`anthropic` / `google-generativeai`) is imported lazily via the
existing adapters, so it's only required for the provider you actually use.
"""

import os
from dataclasses import dataclass
from typing import Any, List, Optional

from mycelium_sdk.logging import get_logger

log = get_logger("agent_loop")

# Default models per provider. Anthropic defaults to the latest Opus; callers
# override via `model=` for a cheaper/faster tier.
_DEFAULT_MODELS = {
    "anthropic": "claude-opus-4-8",
    "gemini": "gemini-2.0-flash",
}
_DEFAULT_MAX_TOKENS = 16000


@dataclass
class ContractTool:
    """A contract function to expose to the model as a tool.

    `contract_id` defaults to the loop's `contract_id`. `read_only=True` marks a
    view/getter (simulated, no fee). `description` helps the model decide when to
    call it — be prescriptive about *when*, not just what.
    """
    function_name: str
    contract_id: Optional[str] = None
    read_only: bool = False
    description: Optional[str] = None


def _normalize_tools(tools, default_contract_id) -> List[ContractTool]:
    """Accept ContractTool instances or bare function-name strings."""
    out: List[ContractTool] = []
    for t in tools or []:
        if isinstance(t, ContractTool):
            out.append(ContractTool(
                t.function_name, t.contract_id or default_contract_id, t.read_only, t.description
            ))
        elif isinstance(t, str):
            out.append(ContractTool(t, default_contract_id))
        else:
            raise TypeError(f"tools entries must be ContractTool or str, got {type(t).__name__}")
    return out


def run_agent_loop(
    goal: str,
    *,
    context,
    provider: str = "anthropic",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    contract_id: Optional[str] = None,
    tools=None,
    hive=None,
    system: Optional[str] = None,
    max_steps: int = 8,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
) -> str:
    """Run an LLM agent loop wired to on-chain tools, returning the final text."""
    provider = provider.lower()
    model = model or _DEFAULT_MODELS.get(provider)
    contract_id = contract_id or os.environ.get("MYCELIUM_CONTRACT_ID") or None
    contract_tools = _normalize_tools(tools, contract_id)

    if provider == "anthropic":
        return _run_anthropic(
            goal, context, model, api_key, contract_tools, hive, system, max_steps, max_tokens
        )
    if provider == "gemini":
        return _run_gemini(goal, context, model, api_key, contract_tools, hive, system)
    raise ValueError(f"Unsupported provider '{provider}'. Use 'anthropic' or 'gemini'.")


# ── Anthropic (manual agentic loop) ──────────────────────────────────────────
def _run_anthropic(goal, context, model, api_key, contract_tools, hive, system, max_steps, max_tokens):
    from mycelium_sdk.adapters import anthropic as adapter

    anthropic = adapter.require_anthropic()
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    schemas: List[dict] = []
    dispatchers = {}  # tool name -> callable(input_dict) -> str
    for t in contract_tools:
        desc = t.description or f"Invoke '{t.function_name}' on the Soroban contract."
        schemas.append(adapter.contract_tool_schema(t.function_name, desc, t.contract_id))
        dispatchers[t.function_name] = adapter.make_tool_dispatcher(
            context, t.function_name, t.contract_id, read_only=t.read_only
        )
    if hive is not None:
        schemas.append(_resolve_tool_schema())
        dispatchers["lookup_partner_agent"] = _make_resolve_dispatcher(hive)

    messages = [{"role": "user", "content": goal}]
    final_text = ""
    for step in range(max_steps):
        kwargs = dict(model=model, max_tokens=max_tokens, messages=messages)
        if schemas:
            kwargs["tools"] = schemas
        if system:
            kwargs["system"] = system
        response = client.messages.create(**kwargs)

        # Always capture any text the model emitted this turn.
        text = "".join(b.text for b in response.content if b.type == "text")
        if text:
            final_text = text

        if response.stop_reason == "refusal":
            log.warning("[agent_loop] model refused the request.")
            return final_text or "(model refused the request)"
        if response.stop_reason != "tool_use":
            return final_text

        # Execute every requested tool and feed all results back in one user turn.
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            dispatch = dispatchers.get(block.name)
            out = dispatch(block.input) if dispatch else f"Error: unknown tool {block.name}."
            log.info(f"[agent_loop] tool {block.name} -> {out}")
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
        messages.append({"role": "user", "content": results})

    log.warning(f"[agent_loop] hit max_steps={max_steps} without a final answer.")
    return final_text


def _resolve_tool_schema() -> dict:
    return {
        "name": "lookup_partner_agent",
        "description": "Look up a registered agent by its unique name in the Hive Registry.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name_tag": {"type": "string", "description": "The agent's unique registry name."}
            },
            "required": ["name_tag"],
        },
    }


def _make_resolve_dispatcher(hive):
    def dispatch(tool_input: dict) -> str:
        try:
            meta = hive.resolve_agent(tool_input.get("name_tag", ""))
            return f"Agent found. Public Key: {meta['public_key']}, Endpoint: {meta['endpoint']}"
        except Exception:
            return "Agent name not registered in the Hive Registry."
    return dispatch


# ── Gemini (automatic function calling) ──────────────────────────────────────
def _run_gemini(goal, context, model, api_key, contract_tools, hive, system):
    from mycelium_sdk.adapters import gemini as adapter

    genai = adapter.require_genai()
    if api_key:
        genai.configure(api_key=api_key)

    fns = [
        adapter.make_contract_function(context, t.function_name, t.contract_id, read_only=t.read_only)
        for t in contract_tools
    ]
    if hive is not None:
        fns.append(adapter.make_resolve_agent_function(hive))

    model_obj = genai.GenerativeModel(
        model_name=model, tools=fns or None, system_instruction=system
    )
    chat = model_obj.start_chat(enable_automatic_function_calling=True)
    response = chat.send_message(goal)
    return response.text
