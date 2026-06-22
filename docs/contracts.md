# Mycelium Contracts & Demos Internals

This document covers the on-chain smart contracts and coordination demos included in the Mycelium repository:
1. **Hive Registry Contract** (`hive_registry.py`)
2. **Escrow Contract** (`escrow_contract.py`)
3. **Agent-to-Agent Demo** (`a2a_demo.py`)

---

## 🐝 Hive Registry Contract (`hive_registry.py`)

The Hive Registry acts as the decentralized DNS for the Mycelium agent network. It maps an alphanumeric name to the agent's public key (G-address), functional capability hash, service endpoint URL, and a reputation score.

### Data Storage Schema
All fields are stored in the contract instance storage:
- `addr:<name>`: Address (Stellar wallet public key).
- `cap:<name>`: Bytes (SHA-256 capability hash).
- `endp:<name>`: Bytes (UTF-8 bytes of the service endpoint).
- `rep:<name>`: U64 (Reputation score, starts at 0).
- `reg:<name>`: Bool (Registration status, to prevent overwrite).

### Error Codes
```python
class ContractError:
    NAME_TAKEN = 1       # Thrown when attempting to register a claimed name
    NOT_REGISTERED = 2   # Thrown when resolving an unregistered name
```

### Method Implementations
- **`register_agent(name, agent_address, capability_hash, endpoint)`**:
  1. Asserts caller authorization: `agent_address.require_auth()`.
  2. Asserts name is free: Reverts if `reg:<name>` exists.
  3. Writes attributes and registers name mapping.
  4. Emits `agent_registered` event.
- **`resolve_agent(name)`**:
  - Read-only view returning a `Map` containing `address`, `capability`, `endpoint`, and `reputation`.
- **`update_reputation(name, new_reputation)`**:
  - Updates the reputation score.
- **`is_registered(name)`**:
  - Returns boolean state of registration.

---

## 🔒 Escrow Contract (`escrow_contract.py`)

The Escrow contract implements the x402 machine-to-machine payment protocol. It locks payment from a depositor to a service provider until a verification proof matching a target SHA-256 hash is published, or a timeout is reached.

### Data Storage Schema
- `depositor`: Address (Payer).
- `provider`: Address (Service operator).
- `token`: Address (Stellar Asset Contract / SEP-41 token).
- `amount`: I128 (Escrow balance).
- `task_hash`: Bytes (SHA-256 hash of task requirements).
- `deadline`: U64 (Timestamp after which refund is permitted).
- `settled`: Bool (Lock flag preventing double claims).
- `init`: Bool (Initialization flag).

### Error Codes
```python
class ContractError:
    ALREADY_INITIALIZED = 1
    NOT_INITIALIZED = 2
    ALREADY_SETTLED = 3
    INVALID_PROOF = 4
    NOT_EXPIRED = 5
```

### Method Implementations
- **`initialize(depositor, provider, token, amount, task_hash, timeout)`**:
  1. Calls `depositor.require_auth()` to authorize transfer.
  2. Runs `env.transfer` pulling tokens from the depositor into the escrow contract.
  3. Sets deadline to `ledger.timestamp() + timeout`.
- **`claim_funds(proof)`**:
  1. Asserts initialization and that `settled` is false.
  2. Verifies proof: Asserts `env.crypto().sha256(proof) == task_hash`.
  3. Runs `env.transfer` transferring tokens from escrow contract to provider.
  4. Sets `settled` to true.
- **`refund()`**:
  1. Asserts deadline has passed: `ledger.timestamp() >= deadline`.
  2. Calls `depositor.require_auth()`.
  3. Runs `env.transfer` returning tokens from escrow contract to depositor.
  4. Sets `settled` to true.

---

## 🏃 Agent-to-Agent Demo (`a2a_demo.py`)

The demo script illustrates on-chain coordination between two independent agents (`testsdk` and `testsdk2`) on Stellar testnet:

### 1. Stateless Discovery
Each agent resolves the other's directory details from the Hive Registry. It loads the partner's address and service endpoint live from the ledger:
```python
a1_seen_by_a2 = hive2.resolve_agent(AGENT1["name"])
```

### 2. Stateful Interaction
Agent 2 calls a state-changing method on Agent 1's contract (in this case, an `add` method), modifying its count. Agent 1 then reads back the updated count, showing coordination via shared ledger state.

### 3. Value Exchange (M2M Settlement)
Agent 2 issues a payment transaction sending XLM to Agent 1. The destination public key is resolved dynamically from the registry instead of being hardcoded.
```python
dest = a1_seen_by_a2["public_key"]
tx = (
    TransactionBuilder(src_account, network_passphrase)
    .append_payment_op(destination=dest, amount="3")
    .build()
)
```
