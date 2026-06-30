# Mycelium

**The Python-first framework for smart-contract development and agentic orchestration on Stellar/Soroban.**

Installing `mycelium` gives you the whole toolchain in one shot:

- `import mycelium` — the contract-authoring DSL (`@contract`, `@external`, `@view`, typed primitives) plus the SDK facade (`AgentContext`, `HiveClient`, x402).
- `import mycelium_sdk` — the on-chain agent SDK (signing, live Soroban contract calls, hive discovery, escrow/x402, AI adapters).
- the `mycelium` CLI command — `init`, `newwallet`, `compile`, `check`, `deploy`, `register`, `agent`.
- the Python → Soroban-WASM compiler (now `0.4.0`, rejoined the unified version).

As of **v0.4.0** this bundle ships the **proof layer** — verifiable agent work
where a bounty is released by a multi-LLM judge panel scoring the real deliverable
against on-chain checks (see `mycelium_sdk.proof` and the `mycelium job` /
`mycelium verifier` CLI groups).

## Install

```bash
pip install mycelium
# optional AI-framework adapters:
pip install "mycelium[langgraph]"   # or [gemini] / [anthropic]
```

## Quickstart

```bash
mycelium init my_agent
cd my_agent
mycelium newwallet
mycelium compile
mycelium deploy --network testnet
mycelium register
```

```python
from mycelium import AgentContext, HiveClient

ctx = AgentContext(keypair_path=".mycelium/wallet.json", network_type="testnet")
hive = HiveClient(ctx)
hive.register("sentinel_alpha", ["data-analysis"], "https://sentinel.example/api")
print(hive.resolve_agent("sentinel_alpha"))
```

The `mycelium compile` / `deploy` commands need the `stellar` CLI (v27.0.0); the
compiler auto-downloads it on first use.

Licensed under MIT.
