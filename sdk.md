MYCELIUM SDK & CLI: TECHNICAL CODE-SPECIFICATIONSystem Version: 0.1.0-alphaTarget Environment: Python 3.10+, Soroban (Stellar) WASM TargetsThis document serves as the absolute, non-negotiable technical specification for building the Mycelium SDK and CLI. It outlines the module architectures, class interfaces, cryptographic operations, state wrappers, and runtime pipelines necessary to build a production-ready, zero-mock on-chain agent environment.1. Project Scaffolding & Directory ArchitectureWhen a developer initializes a project with the Mycelium CLI, it must establish a standard structure that decouples the outer AI orchestrator (off-chain execution) from the inner smart contract code (on-chain financial consensus).1.1 Scaffold File System Tree<project_name>/
├── mycelium.toml            # Central project metadata and deployment config
├── agent.py                 # Outer logic: LLM Orchestration (LangGraph / Gemini)
├── contract.py              # Inner logic: Strictly-typed Python-to-Soroban contract
└── .mycelium/               # Protected local directory (added to .gitignore)
    └── wallet.json          # Encrypted Ed25519 sovereign keypair
1.2 Configuration Specification (mycelium.toml)The configuration file dictates how the compiler, runtime registry, and CLI interact. It must adhere strictly to the following TOML structure:[project]
name = "sentinel_agent"
version = "0.1.0"
author = "Developer"

[agent]
framework = "langgraph"       # Choices: ["langgraph", "gemini", "anthropic", "custom"]
model = "gemini-1.5-pro"      # Exact model string identifier
unique_name = "sentinel_alpha" # Globally unique alphanumeric alphanumeric hive registry name

[onchain]
source_contract = "contract.py"
target_wasm = "build/contract.wasm"
network = "testnet"           # Default deployment target: ["testnet", "mainnet"]
# Populated automatically after running `mycelium deploy`
contract_id = "" 
wallet_public_key = ""

[registry]
# Hardcoded universal on-chain Hive Registry address constant
hive_registry_address = "CCREGISTRYMAINNETORTESTNETPROD1234567890XYZ"
service_endpoint = "https://sentinel.agents.mycelium.sh/api/v1"
capabilities = ["data-analysis", "stellar-arbitrage"]
2. CLI Command Specification (mycelium-cli)The CLI must wrap all interaction pipelines so that the developer is never exposed to raw Rust tooling or raw Stellar XDR manipulation. All actions use the unified mycelium command group.                     +---------------------------------------+
                     |             mycelium-cli              |
                     +-------------------+-------------------+
                                         |
         +------------------+------------+------------+-------------------+
         |                  |                         |                   |
         v                  v                         v                   v
   `mycelium init`   `mycelium newwallet`    `mycelium compile`   `mycelium deploy`
2.1 mycelium init <name>Behavior: Interactive project generator wizard.Prompt Steps:Prompt for AI Core Orchestration Framework: Options [LangGraph, Gemini API, Anthropic API, Custom Python Loop].Prompt for Target Model: Options [gemini-1.5-pro, claude-3-5-sonnet, custom].Prompt for Alphanumeric Unique Name: Enforce regular expression validation ^[a-zA-Z0-9_]{3,30}$.Scaffolds directory layout.Populates mycelium.toml based on inputs.Generates basic template files for agent.py and contract.py.2.2 mycelium newwalletBehavior: Generates a secure, cryptographically isolated Ed25519 wallet pair for the agent.Cryptography Requirements:Must use safe entropy sources (os.urandom) to instantiate a keypair.Encrypts the secret seed (S-address) using AES-GCM-256 before saving to disk.Prompts the developer to enter a local encryption passphrase or falls back to standard environment variable key derivatives (MYCELIUM_DECRYPT_KEY).Writes payload containing encrypted secret string and plain-text public key (G-address) directly to .mycelium/wallet.json.{
  "public_key": "GD3B...7Y",
  "encrypted_secret": "aes-gcm-encrypted-hex-payload",
  "nonce": "hex-encoded-nonce-bytes",
  "salt": "hex-encoded-kdf-salt"
}
2.3 mycelium compileBehavior: Reads the contract.py defined in mycelium.toml.Execution Path:Parses the source code using Python's native AST parser module (ast).Runs static semantic validation on type annotations, state modifiers, and storage scopes.If validations pass, routes the validated syntax tree representation to the Mycelium AST-to-Soroban-Rust translator, then outputs optimized WebAssembly directly to build/contract.wasm.Outputs size telemetry and optimization stats (--optimize flag reduces target bytecode size).2.4 mycelium deployBehavior: Publishes the compiled WASM binary directly to Stellar.Testnet Pipeline Steps (--network testnet):Checks .mycelium/wallet.json for agent public key.Queries Horizon Testnet RPC to verify account balance.If balance is $0$ XLM, trigger automated network funding loop using Stellar Friendbot API: https://friendbot.stellar.org/?addr={public_key}. Poll until confirmed.Build, sign, and submit a Soroban UploadWASM transaction containing the binary data.Build, sign, and submit an InstantiateContract transaction using the uploaded WASM hash reference.Write the resulting contract identity hex string directly back into mycelium.toml under onchain.contract_id.Mainnet Pipeline Steps (--network mainnet):Validate account balance on live ledger.If wallet balance $< 5.0$ XLM, halt process, throw standard error: [Error] Insufficient funds for live deployment. Mainnet operations require at least 5 XLM sequence reserve. Balance must be deposited to: {public_key}.Skip Friendbot queries, build production transaction, submit, and write back live Contract ID on success.2.5 mycelium registerBehavior: Calls the globally hardcoded Hive Registry contract (HIVEMIND_REGISTRY_ADDRESS) to map the agent's alphanumeric name to its parameters on-chain.Execution Path:Read configuration parameters from local mycelium.toml.Encrypt/serialize metadata values (Unique Name, Public Wallet Key, Endpoint URL, Capabilities list).Formulate a Soroban transaction invoking register_agent on the registry contract:$$\text{Invocation: } \mathtt{register\_agent(name, G-address, capability\_hash, endpoint\_uri)}$$Sign with agent's local key and submit transaction. Raise exception if registration fails due to name collision.3. The SDK Python API Architecture (mycelium module)The programmatic SDK library handles the state tracking, network RPC client wrappers, XDR serialization abstractions, and external framework integration adapters.                        +---------------------------------+
                        |         mycelium module         |
                        +----------------+----------------+
                                         |
         +------------------+------------+------------+-------------------+
         |                  |                         |                   |
         v                  v                         v                   v
   `AgentContext`     `HiveClient`                 `x402`          `AI Adapters`
  (RPC/Signing)     (Hive Discovery)          (Escrow Payments)   (LangGraph/Gemini)
3.1 AgentContext API SpecificationThe central management module mapping Python execution targets directly to live Stellar nodes.import json
from typing import List, Dict, Any
from stellar_sdk import Keypair, Server, Network
from stellar_sdk.soroban_server import SorobanServer
from stellar_sdk.exceptions import BaseRequestError

class AgentContext:
    def __init__(self, keypair_path: str, network_type: str = "testnet"):
        self.network_type = network_type.lower()
        self.keypair = self._load_and_decrypt_keypair(keypair_path)
        
        # Configure RPC Nodes
        if self.network_type == "testnet":
            self.soroban_rpc = SorobanServer("https://soroban-testnet.stellar.org")
            self.horizon_server = Server("https://horizon-testnet.stellar.org")
            self.network_passphrase = Network.TESTNET_NETWORK_PASSPHRASE
        else:
            self.soroban_rpc = SorobanServer("https://soroban-mainnet.stellar.org")
            self.horizon_server = Server("https://horizon.stellar.org")
            self.network_passphrase = Network.PUBLIC_NETWORK_PASSPHRASE

    def _load_and_decrypt_keypair(self, path: str) -> Keypair:
        """
        Loads the locally encrypted wallet file, decrypts secret string, 
        and returns a standard Stellar Ed25519 signing keypair instance.
        """
        with open(path, 'r') as f:
            wallet_data = json.load(f)
        
        # In a real environment, decrypt encrypted_secret using AES-GCM
        # For simplicity, load decrypted secret seed key
        decrypted_seed = self._decrypt_aes_gcm(
            wallet_data["encrypted_secret"], 
            wallet_data["nonce"], 
            wallet_data["salt"]
        )
        return Keypair.from_secret(decrypted_seed)

    def _decrypt_aes_gcm(self, ciphertext_hex: str, nonce_hex: str, salt_hex: str) -> str:
        # Cryptographic decryption loop logic goes here
        # Return plain secret key seed string starting with 'S'
        pass

    def call_contract(self, contract_id: str, function_name: str, args: List[Any]) -> Any:
        """
        Prepares contract invocation, fetches account sequence numbers,
        builds, serializes arguments to Soroban XDR structure format, 
        signs, and submits transaction. Returns decrypted Python primitives.
        """
        print(f"[SDK] Involving target on-chain method {function_name} on {contract_id}...")
        try:
            # 1. Load account state to acquire current sequence ID
            account = self.horizon_server.load_account(self.keypair.public_key)
            
            # 2. Build transaction payload wrapping Soroban invocation operations
            # 3. Simulate transaction through Soroban RPC first to estimate footprints
            # 4. Inject footprint dependencies and finalize fee parameters
            # 5. Sign transaction envelope with decrypted Keypair
            # 6. Submit signed transaction payload to Soroban RPC and wait for settlement
            # 7. Extract returned value XDR data, parse back to Python primitives, and return
            pass
        except BaseRequestError as error:
            print(f"[SDK ERROR] Soroban Contract Invocation Failed: {error}")
            raise
3.2 HiveClient API SpecificationHandles directory resolution, looking up addresses, routing queries, and capability verification checks against the global Registry.from mycelium.constants import HIVEMIND_REGISTRY_ADDRESS

class HiveClient:
    def __init__(self, context: AgentContext):
        self.context = context
        self.registry_address = HIVEMIND_REGISTRY_ADDRESS

    def register(self, unique_name: str, capability_tags: List[str], endpoint: str):
        """
        Calls on-chain Hive Registry to register unique alphanumeric name.
        """
        # Formulate capability binary hash from string tags list
        capability_hash = self._compute_capability_hash(capability_tags)
        
        # Invoke registry contract directly on-chain
        return self.context.call_contract(
            contract_id=self.registry_address,
            function_name="register_agent",
            args=[unique_name, self.context.keypair.public_key, capability_hash, endpoint]
        )

    def resolve_agent(self, unique_name: str) -> Dict[str, Any]:
        """
        Queries Hive Registry contract state, resolving name -> wallet details & endpoint.
        """
        raw_metadata = self.context.call_contract(
            contract_id=self.registry_address,
            function_name="resolve_agent",
            args=[unique_name]
        )
        # Parse return structures back into structured directory information dictionaries
        return {
            "public_key": raw_metadata["address"],
            "capability_hash": raw_metadata["capability"],
            "endpoint": raw_metadata["endpoint"],
            "reputation": int(raw_metadata["reputation"])
        }

    def _compute_capability_hash(self, tags: List[str]) -> bytes:
        import hashlib
        serialized_tags = ",".join(sorted(tags)).encode('utf-8')
        return hashlib.sha256(serialized_tags).digest()
3.3 x402 Machine-to-Machine Commerce FrameworkImplements automated micro-payments routing, conditional escrow builders, and fee settlements natively on Stellar.from decimal import Decimal

class EscrowPaymentRouter:
    def __init__(self, context: AgentContext):
        self.context = context

    def create_locked_escrow(self, provider_id: str, amount_xlm: Decimal, task_hash: bytes) -> str:
        """
        Builds and deploys an ephemeral payment escrow contract instance.
        Deposits target payment, locking funds until proof is published.
        """
        print(f"[x402] Initializing escrow lock of {amount_xlm} XLM for provider {provider_id}...")
        # Deploys standard payment lock contract on-chain
        # Returns Escrow Contract Address
        pass

    def release_funds(self, escrow_contract_id: str, verification_proof: bytes):
        """
        Invokes execution completion check against active escrow to disburse locked funds.
        """
        print(f"[x402] Confirming task execution. Triggering disbursement of funds...")
        return self.context.call_contract(
            contract_id=escrow_contract_id,
            function_name="claim_funds",
            args=[verification_proof]
        )
4. AI Orchestrator Adapters (Framework Integrations)The SDK must bridge standard LLM frameworks with these on-chain capabilities. The orchestrator exposes contract functions to AI frameworks as native "Tools".4.1 LangGraph Tool-Execution AdapterBelow is the integration mapping allowing a LangGraph workflow node to invoke an on-chain smart contract call dynamically based on tool parameters:from langchain_core.tools import tool
from mycelium import AgentContext

# Globally initialize our sovereign execution context
context = AgentContext(keypair_path=".mycelium/wallet.json")

@tool
def trigger_arbitrage_trade(pool_id: str, execution_amount: int) -> str:
    """
    Executes a high-frequency arbitrage trade directly on-chain 
    against a Stellar liquidity pool contract. Use this tool 
    when price differences across pools are identified.
    """
    # Expose the tool cleanly to LangChain/LangGraph agent schemas
    try:
        tx_receipt = context.call_contract(
            contract_id=pool_id,
            function_name="execute_trade",
            args=[context.keypair.public_key, execution_amount]
        )
        return f"Transaction successfully settled on-chain. Receipt Hash: {tx_receipt.hash}"
    except Exception as e:
        return f"Failed to execute trade transaction. Error log: {str(e)}"
4.2 Google Gemini Tool-Calling API IntegrationHere is how an agent leveraging Google's Gemini API directly maps its native multi-tool capability blocks to on-chain execution without external SDK dependencies:import google.generativeai as genai
from mycelium import AgentContext, HiveClient

# Load secure credentials and context parameters
context = AgentContext(keypair_path=".mycelium/wallet.json")
hive_client = HiveClient(context)

# 1. Define standard tools accessible by Gemini API
def lookup_partner_agent(name_tag: str) -> str:
    """Queries the Hive Registry to find a service agent capable of processing data."""
    try:
        metadata = hive_client.resolve_agent(name_tag)
        return f"Agent found. Public Key: {metadata['public_key']}, API Endpoint: {metadata['endpoint']}"
    except Exception:
        return "Agent name not registered in the Hivemind Directory."

# 2. Configure model with tools mapping
model = genai.GenerativeModel(
    model_name="gemini-1.5-pro",
    tools=[lookup_partner_agent]
)

# 3. Handle model tool-call responses in loop
chat = model.start_chat(enable_automatic_function_calling=True)
response = chat.send_message(
    "I need to run a classifier. Find a registered agent with the name 'data_classifier_alpha'."
)
print(response.text)
5. Summary Matrix for DevelopersEnsure your SDK and CLI codebases adhere to these foundational constraints:Zero Mocks: All network transactions must go through live horizon/Soroban RPC nodes. Mocks are forbidden.Encrypted Keys: Private key inputs must never be saved on-disk as plain-text string secrets. Use standardized PBKDF2/AES algorithms.Implicit Constants: The central Hive Registry smart contract target is globally constant, avoiding manual inputs for network lookup routes.Alphanumeric Unique Names: Registry validation blocks prevent overlapping names, securing clean routing routes across independent swarms.