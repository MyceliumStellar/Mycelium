# Mycelium SDK

The Mycelium SDK provides a clean, Python-first interface for orchestrating autonomous agents, verifying cryptographically signed payloads, querying the Swarm Hive Registry, and settling M2M payments via the x402 Commerce Protocol on the Stellar/Soroban network.

## Installation

The SDK can be installed directly from PyPI (or via the wrapper `mycelium-stellar` package):
```bash
pip install mycelium-sdk
```

---

## Core Architecture

The SDK handles all off-chain agent logic, cryptography, AI orchestration, and RPC interactions with Soroban.

```
                  ┌──────────────────────────────┐
                  │          AI Framework        │
                  │   (LangGraph/Gemini/etc.)    │
                  └──────────────┬───────────────┘
                                 │
                                 ▼
                  ┌──────────────────────────────┐
                  │         Mycelium SDK         │
                  │ (AgentContext & HiveClient)  │
                  └──────────────┬───────────────┘
                                 │
                                 ▼
                  ┌──────────────────────────────┐
                  │    Stellar Soroban Network   │
                  │    (RPC, Ledger Queries)     │
                  └──────────────────────────────┘
```

---

## Primary APIs

### 1. `AgentContext`
Manages on-chain identity, cryptographic keypairs, and transaction orchestration.
* `AgentContext(keypair_path: str, network_type: str = "testnet")`
  - Loads an encrypted wallet keypair from local storage.
* `AgentContext.read_only(network_type: str = "testnet")`
  - Initializes a read-only context (does not require a keypair; ideal for registry scans).
* `AgentContext.from_keypair(keypair: Keypair, network_type: str = "testnet")`
  - Initializes a context from an in-memory `stellar_sdk.Keypair` object.
* `call_contract(contract_id: str, function_name: str, args: list, send: bool = False)`
  - Invokes an on-chain smart contract function.
* `acall_contract(contract_id: str, function_name: str, args: list)`
  - Asynchronous contract invocation wrapper.

### 2. `HiveClient`
Interfaces with the on-chain Hive Registry to register, discover, and resolve agents.
* `register_agent(name: str, capability_hash: str, endpoint: str)`
  - Registers the agent's unique name, capabilities, and HTTPS service endpoint.
* `resolve_agent(name: str) -> dict`
  - Resolves an agent name to its public address, capabilities, endpoint, and reputation.
* `lookup_partner_agent(capability: str) -> list[dict]`
  - Scans the ledger to discover agents matching a specific service capability.

### 3. `EscrowPaymentRouter` (x402 Commerce)
Manages multi-agent escrow settlements and trustless commerce routing.
* `create_locked_escrow(recipient: str, amount: str, token: str = None) -> str`
  - Locks funds on-chain under an escrow contract router.
* `release_escrow(escrow_id: str, signature: str)`
  - Releases locked funds to the recipient agent after cryptographic validation.
* `refund_escrow(escrow_id: str)`
  - Reclaims locked funds after a predetermined expiry period.
* **Note**: `EscrowPaymentManager` is maintained as a backward-compatible alias.

### 4. `run_agent_loop`
Executes autonomous agent orchestration loops wired to cloud LLM APIs (Anthropic, Gemini, etc.) and exposes on-chain interactions as executable LLM tools.

---

## Code Example: Autonomous Payment Agent

```python
import os
from mycelium import AgentContext, HiveClient, run_agent_loop, ContractTool

# Load sovereign on-chain identity
context = AgentContext(keypair_path=".mycelium/wallet.json", network_type="testnet")
hive = HiveClient(context)

# On-chain contract ID
CONTRACT_ID = os.environ.get("MYCELIUM_CONTRACT_ID")

def main():
    print(f"Agent online as: {context.keypair.public_key}")
    
    # Run the autonomous execution loop
    response = run_agent_loop(
        "Scan the registry for an agent offering translation capabilities, "
        "negotiate a settlement, and execute the payment.",
        context=context,
        provider="gemini",
        model="gemini-1.5-pro",
        api_key=os.environ.get("GEMINI_API_KEY"),
        contract_id=CONTRACT_ID,
        tools=[
            ContractTool("increment"),
            ContractTool("get_count", read_only=True),
        ],
        hive=hive
    )
    print(f"Loop Response:\n{response}")

if __name__ == "__main__":
    main()
```
