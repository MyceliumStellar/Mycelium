---
name: agent-skill
description: Scaffolds Mycelium, installs the SDK/CLI, initializes wallets, and sets up autonomous agent loops step-by-step.
---

# Mycelium Agent Skill

This skill guides you (the AI assistant) to assist a **vibecoder** in setting up the Mycelium framework, managing wallets, and programming autonomous agent loops without requiring them to write complex boilerplate code.

---

## 🛠️ Step 1: Environment Setup

Always verify and set up the Python virtual environment before running Mycelium commands.

1. **Create Virtual Environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
2. **Install Mycelium Stellar Bundle**:
   Install the full package containing the DSL, SDK, CLI, and compiler:
   ```bash
   pip install mycelium-stellar==0.4.0
   ```
3. **Verify Installation**:
   ```bash
   mycelium --help
   ```

---

## 🔑 Step 2: Wallet Scaffolding

Agents need a secure keypair to interact on-chain (Stellar Testnet).

1. **Initialize Project Config**:
   Initialize a new workspace (creates `mycelium.toml` with testnet configurations):
   ```bash
   mycelium init
   ```
2. **Create Secure Wallet**:
   Create a new encrypted wallet. Choose a passkey and store it in environment variables or configuration.
   ```bash
   mycelium newwallet
   ```
   *Note: This generates `.mycelium/wallet.json`.*
3. **Fund the Wallet**:
   Retrieve testnet XLM from the Friendbot faucet to pay for contract transaction fees:
   ```bash
   mycelium fund
   ```
4. **Check Balance**:
   ```bash
   mycelium status
   ```

---

## 🤖 Step 3: Scaffolding a Basic Agent Loop

Generate the Python boilerplate for the agent. The vibecoder wants a single script that connects to the LLM and runs tool execution on-chain.

Create `agent_loop.py` with the following template:

```python
import os
from mycelium import AgentContext, HiveClient, run_agent_loop, ContractTool

# 1. Initialize encrypted context (reads .mycelium/wallet.json)
# Prompt user for password or load from env
passphrase = os.getenv("MYCELIUM_PASSPHRASE", "your-wallet-passphrase")
context = AgentContext(".mycelium/wallet.json", passphrase=passphrase)
hive = HiveClient(context)

print(f"Agent loaded. On-chain Address: {context.keypair.public_key}")

# 2. Define the agent's goal and available on-chain tools
goal = "Query the counter state, increment it, and verify the new total."
counter_contract = "CDASJ42STDU42QXDXH3KRFNQWBURB54XPXV2WBXHWGPBA2BNAI5EYULO" # Replace with target

tools = [
    ContractTool(
        function_name="increment",
        description="Increments the on-chain counter by 1. Requires gas fee.",
        contract_id=counter_contract
    ),
    ContractTool(
        function_name="get_count",
        read_only=True,
        description="Returns the current total counter state. Read-only.",
        contract_id=counter_contract
    )
]

# 3. Start execution loop using Gemini or Anthropic
final_verdict = run_agent_loop(
    goal=goal,
    context=context,
    provider="gemini", # Or "anthropic", "openai"
    tools=tools,
    hive=hive,
    max_steps=5
)

print(f"\nFinal Agent Output:\n{final_verdict}")
```

---

## 💾 Step 4: Persistent Memory Configuration

For stateful agents, configure persistent off-chain memory with on-chain cryptographic anchoring.

1. **Initialize Memory Anchor**:
   Save a memory key/value state locally and anchor it on-chain:
   ```bash
   mycelium memory remember "goal" "Build a payment router on Stellar"
   mycelium memory anchor --uri "https://storage.mycelium.network/mem_1.json"
   ```
2. **Rehydrate Memory on another machine**:
   ```bash
   mycelium memory rehydrate --owner "G..." --uri "https://storage.mycelium.network/mem_1.json"
   ```
