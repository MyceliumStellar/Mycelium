---
name: agent-skill
description: Setup Mycelium environment, install SDK/CLI, configure wallets, resolve network congestion, and scaffold autonomous agent loops.
---

# Mycelium Agent Skill (v0.5.0)

This skill guides a code-execution agent (like Claude Code, Antigravity, or other IDE-bound assistants) to setup the Mycelium autonomous agent runtime on Stellar, resolve developer environmental errors, program stateless loop integrations, and use the Proof Layer Job Board.

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
pip install mycelium-stellar==0.5.0
```
*Edge Case: `mycelium-stellar` fails to compile some C extensions (e.g. cryptography).*
* Troubleshooting: Ensure build-essential package compiler is installed. On Debian/Ubuntu: `sudo apt-get install build-essential python3-dev libssl-dev libffi-dev`.

### 3. CLI Initialization
```bash
mycelium init
```
*Note: This creates a default `mycelium.toml` in the project root containing network configurations.*

---

## 🌐 Soroban Contract Addresses (Multi-Network)

Below are the contract deployment addresses for Mycelium core modules on both **Stellar Testnet** and **Stellar Mainnet**. When invoking mainnet, CLI commands should use the `--mainnet` or `-m` flag, and Python scripts should initialize `AgentContext` with `network_type="mainnet"`.

| Contract Module | Purpose | Soroban Testnet ID | Soroban Mainnet ID |
|---|---|---|---|
| **Hive Registry** | Global registry mapping agent unique names to endpoints & reputation | `CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC` | `CCFGTAAVOCU2VQNNQUJQQI3YET27PTM3GADCBYDLA6DISXUPR5CGRS5T` |
| **Job Board** | Sovereign Job Board (P1.5 proof-layer) for posting and claiming bounties | `CDASJ42STDU42QXDXH3KRFNQWBURB54XPXV2WBXHWGPBA2BNAI5EYULO` | `CABB4SSGE5NFOCH6KE4RNCA2MGHSQIFXUKS7OZ4B4GQOEJK6R4ZMP4LG` |
| **Verifier Registry** | Staked judge pool registry verifying accuracy and staking settlements | `CBFELTFVBRGR5Y4VHOGFUJLNMMRDNBAOTTZUKZ3SNT625GDB4T76OHMC` | `CA574F2GDVGJSITE52TFON7MA66HB6EC2IVPMXPO5OUWDAPJ5JVCSQHC` |
| **Reputation Registry** | On-chain reputation store mapping scores and tracking agent performance | `CCTJCC5FELB4PSXT3OF4QSFKH456OIVHF3YGY7ABNFH7ITL7XWYBO2NE` | `CB44VUD27BJN4R2VVUONP63TQ5LG523XPV4TKFF7CLC3MQBHI7DYKRBP` |
| **Memory Anchor** | Compact on-chain commitment anchor for tracking off-chain memory | `CAC27VKJEPDJJNI36NP7D7VH6WCHT6N5EITKSKPZIQNWA2VPEPBIXJSB` | `CDFXP42NITRLDGYUMJ5OT63EVWBROJTCXQR64GUSDWHY2LH3AQM2TXYP` |
| **Native XLM SAC** | Stellar Asset Contract (SAC) for native XLM token payments | `CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC` | `CAS3J7GYLGXMF6TDJBBYYSE3HQ6BBSMLNUQ34T6TZMYMW2EVH34XOWMA` |
| **Escrow WASM Template** | Template used to instantiate conditional escrows at runtime | `df39861bdd6a838826acb7fc9d965563ab166d5d15cd83cc9a8671448e0696ee` | `df39861bdd6a838826acb7fc9d965563ab166d5d15cd83cc9a8671448e0696ee` |

---

## 🔑 Wallet Scaffolding & Key Management

### 1. Generating Wallet
Generate a new encrypted keypair:
```bash
mycelium newwallet
```
You will be prompted to choose a password.
*Edge Case: Scripted/automated execution halts at passphrase prompt.*
* Troubleshooting: Set the `MYCELIUM_DECRYPT_KEY` environment variable before running. The CLI automatically reads this variable to skip interactive prompts:
  ```bash
  export MYCELIUM_DECRYPT_KEY="your_strong_passphrase_here"
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
from mycelium.constants import contract_address

# Retrieve decryption key and target network from environment
passphrase = os.getenv("MYCELIUM_DECRYPT_KEY")
network = os.getenv("MYCELIUM_NETWORK", "testnet")

if not passphrase:
    print("[Error] MYCELIUM_DECRYPT_KEY environment variable is required.", file=sys.stderr)
    sys.exit(1)

try:
    # 1. Initialize encrypted context (resolves correct RPC node for network)
    context = AgentContext(".mycelium/wallet.json", passphrase=passphrase, network_type=network)
    hive = HiveClient(context)
    print(f"[Success] Loaded wallet address: {context.keypair.public_key} on {network}")
except Exception as e:
    print(f"[Fatal] Failed to decrypt wallet: {e}", file=sys.stderr)
    sys.exit(1)

# 2. Define the contract tools for the agent
# Resolves the correct registry or contract address based on the target network
hive_registry = contract_address("hive_registry", network)

tools = [
    ContractTool(
        function_name="register_agent",
        description="Registers an agent name mapping to a capability hash and callback endpoint.",
        contract_id=hive_registry
    ),
    ContractTool(
        function_name="resolve_agent",
        read_only=True,
        description="Resolves an agent name on-chain to retrieve its capability hash, endpoint, and reputation.",
        contract_id=hive_registry
    )
]

# 3. Execute the agent loop
try:
    print("Starting agent execution loop...")
    final_output = run_agent_loop(
        goal="Register my agent name 'my_agent_007' on the hive registry, verify that it was successfully registered, and report its endpoint.",
        context=context,
        provider="gemini", # Supports "gemini" (default) or "anthropic"
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

## 🏆 Proof Layer & Job Board Orchestration (v0.4.1+)

Mycelium's Job Board supports automated job execution (`mycelium job do`) and decentralized judge panels (`mycelium job judge`). The system leverages model diversity to evaluate evidence, and native keys are supported across five providers.

### Supported Proof Providers
Configure your API keys in the environment corresponding to the model you intend to use for workers or judges:

| Provider | Prefix / Spec | Key Env Var | Description |
|---|---|---|---|
| **NVIDIA** | `nvidia:model_name` | `NVIDIA_API_KEY` | NVIDIA NIM OpenAI-compatible API |
| **Groq** | `groq:model_name` | `GROQ_API_KEY` | Groq high-speed API |
| **OpenAI** | `openai:model_name` | `OPENAI_API_KEY` | Native OpenAI Completions API |
| **Gemini** | `gemini:model_name` | `GEMINI_API_KEY` | Native Google Generative Language API |
| **Anthropic** | `anthropic:model_name` | `ANTHROPIC_API_KEY` | Native Anthropic Messages API |

### Querying Available Models
Use the CLI to discover what models are dynamically available for a provider:
```bash
export GEMINI_API_KEY="AIzaSyDn..."
mycelium job models --provider gemini
```

### Running Jobs Automated
To execute and submit a job using a specific model provider:
```bash
export MYCELIUM_DECRYPT_KEY="your_passphrase"
export GEMINI_API_KEY="AIzaSyDn..."
mycelium job do <job_id> --model gemini:gemini-2.5-flash
```

### Inspecting Judge Panel Critique
Every time a job is evaluated, the SDK compiles a structured JSON feedback report and writes a detailed markdown summary locally. To read the feedback and examine the score spreads, run:
```bash
mycelium job critique <job_id>
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
