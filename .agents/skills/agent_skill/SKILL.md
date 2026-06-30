---
name: agent-skill
description: Setup Mycelium environment, install SDK/CLI, configure wallets, resolve network congestion, and scaffold autonomous agent loops.
---

# Mycelium Agent Skill (v0.4.0)

This skill guides a code-execution agent (like Claude Code, Antigravity, or other IDE-bound assistants) to setup the Mycelium autonomous agent runtime on Stellar Testnet, resolve developer environmental errors, and program stateless loop integrations.

---

## 🛠️ Step-by-Step Installation & Setup

### 1. Environment & Virtual Environment
Always isolate python packages to avoid dependency conflicts.
```bash
python3 -m venv venv
source venv/bin/activate
```
*Edge Case: `python3` command not found.*
* Troubleshooting: Ensure Python 3.10+ is installed on the host OS. On Linux/Debian: `sudo apt-get update && sudo apt-get install -y python3-venv python3-pip`.

### 2. Installing the Metapackage
```bash
pip install --upgrade pip
pip install mycelium-stellar==0.4.0
```
*Edge Case: `mycelium-stellar` fails to compile some C extensions (e.g. cryptography).*
* Troubleshooting: Ensure build-essential package compiler is installed. On Debian/Ubuntu: `sudo apt-get install build-essential python3-dev libssl-dev libffi-dev`.

### 3. CLI Initialization
```bash
mycelium init
```
*Note: This creates a default `mycelium.toml` in the project root containing network configurations.*

---

## 🔑 Wallet Scaffolding & Key Management

### 1. Generating Wallet
Generate a new encrypted keypair:
```bash
mycelium newwallet
```
You will be prompted to choose a password.
*Edge Case: Scripted/automated execution halts at passphrase prompt.*
* Troubleshooting: Set `MYCELIUM_PASSPHRASE` environment variable before running. The CLI automatically reads this variable to skip interactive prompts:
  ```bash
  export MYCELIUM_PASSPHRASE="your_strong_passphrase_here"
  mycelium newwallet
  ```

### 2. Requesting Testnet Funds (Friendbot)
The wallet needs native XLM to pay for transaction gas fees on Soroban:
```bash
mycelium fund
```
*Edge Case: Friendbot rate limit / connection failure.*
* Troubleshooting: If the CLI fails with a connection error or `429 Too Many Requests`, fetch your public key address using `mycelium status` and fund it manually by hitting the Friendbot API directly:
  ```bash
  PUBLIC_KEY=$(mycelium status | grep "Public Key" | awk '{print $NF}')
  curl -X GET "https://friendbot.stellar.org/?addr=${PUBLIC_KEY}"
  ```

---

## 🤖 Programming the Autonomous Agent Loop

Use this exact programmatic framework to build a Python script (`agent_loop.py`) that delegates tasks to LLMs (Gemini/Anthropic) while executing on-chain transactions via tool calling:

```python
import os
import sys
from mycelium import AgentContext, HiveClient, run_agent_loop, ContractTool

# Retrieve passphrase from environment or fail early
passphrase = os.getenv("MYCELIUM_PASSPHRASE")
if not passphrase:
    print("[Error] MYCELIUM_PASSPHRASE environment variable is required.", file=sys.stderr)
    sys.exit(1)

try:
    # 1. Initialize encrypted context (decrypted only in memory)
    context = AgentContext(".mycelium/wallet.json", passphrase=passphrase)
    hive = HiveClient(context)
    print(f"[Success] Loaded wallet address: {context.keypair.public_key}")
except Exception as e:
    print(f"[Fatal] Failed to decrypt wallet: {e}", file=sys.stderr)
    sys.exit(1)

# 2. Define the contract tools for the agent
# Counter contract deployed on Testnet
counter_contract = "CDASJ42STDU42QXDXH3KRFNQWBURB54XPXV2WBXHWGPBA2BNAI5EYULO"

tools = [
    ContractTool(
        function_name="increment",
        description="Calls the increment state function on the contract. Requires transaction gas fee.",
        contract_id=counter_contract
    ),
    ContractTool(
        function_name="get_count",
        read_only=True,
        description="Reads the current total count state. Read-only.",
        contract_id=counter_contract
    )
]

# 3. Execute the agent loop
try:
    print("Starting agent execution loop...")
    final_output = run_agent_loop(
        goal="Increment the counter, check if it succeeded, and report the new total.",
        context=context,
        provider="gemini", # Supports "gemini" (default), "anthropic", "openai", "ollama"
        tools=tools,
        hive=hive,
        max_steps=5
    )
    print(f"\n[Agent Completed]\n{final_output}")
except Exception as loop_error:
    print(f"[Loop Exception] Agent failed during execution: {loop_error}", file=sys.stderr)
    sys.exit(1)
```

---

## ⚠️ Crucial Edge Cases & Troubleshooting

### 1. Network Congestion & Sequence Number Mismatches (`txBAD_SEQ`)
When multiple agent loops submit transactions rapidly, sequence numbers can fall out of sync:
* **SDK Recovery:** The SDK automatically handles reloads and rebuilds/re-signs on `txBAD_SEQ`.
* **CLI Manual Settings:** You can increase the transaction timeout (default 60s) up to 180s by setting the environment variable:
  ```bash
  export MYCELIUM_TX_TIMEOUT=180
  ```

### 2. Virtual Env Port Conflicts (FastAPI & Next.js local servers)
If launching local gateway or dashboard servers:
* **Conflict on port 8000 or 3000:**
  * Find and terminate conflicting processes:
    ```bash
    kill -9 $(lsof -t -i:8000) 2>/dev/null
    kill -9 $(lsof -t -i:3000) 2>/dev/null
    ```
