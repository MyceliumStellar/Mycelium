"""
Network and registry constants — the single source of truth shared by
`AgentContext` (SDK) and the `mycelium deploy` / `mycelium register` CLI commands.

Values mirror the proven configuration used by the IDE backend's `/api/deploy`
endpoint (ide/backend/main.py).
"""

# ─── Stellar / Soroban network endpoints ─────────────────────────────────────
TESTNET = "testnet"
MAINNET = "mainnet"

# ─── Per-network contract addresses ──────────────────────────────────────────
# Every singleton contract deployed once per network. Consumers should call
# `contract_address(name, network)` instead of reading the bare scalars.
# Mainnet values are "__PENDING__" until the contracts are manually deployed;
# the SDK will raise a clear error if code tries to use an un-deployed address.
CONTRACT_ADDRESSES = {
    TESTNET: {
        "hive_registry":       "CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC",
        "job_board":           "CDASJ42STDU42QXDXH3KRFNQWBURB54XPXV2WBXHWGPBA2BNAI5EYULO",
        "memory_anchor":       "CAC27VKJEPDJJNI36NP7D7VH6WCHT6N5EITKSKPZIQNWA2VPEPBIXJSB",
        "verifier_registry":   "CBFELTFVBRGR5Y4VHOGFUJLNMMRDNBAOTTZUKZ3SNT625GDB4T76OHMC",
        "reputation_registry": "CCTJCC5FELB4PSXT3OF4QSFKH456OIVHF3YGY7ABNFH7ITL7XWYBO2NE",
    },
    MAINNET: {
        "hive_registry":       "CCFGTAAVOCU2VQNNQUJQQI3YET27PTM3GADCBYDLA6DISXUPR5CGRS5T",
        "job_board":           "CABB4SSGE5NFOCH6KE4RNCA2MGHSQIFXUKS7OZ4B4GQOEJK6R4ZMP4LG",
        "memory_anchor":       "CDFXP42NITRLDGYUMJ5OT63EVWBROJTCXQR64GUSDWHY2LH3AQM2TXYP",
        "verifier_registry":   "CA574F2GDVGJSITE52TFON7MA66HB6EC2IVPMXPO5OUWDAPJ5JVCSQHC",
        "reputation_registry": "CB44VUD27BJN4R2VVUONP63TQ5LG523XPV4TKFF7CLC3MQBHI7DYKRBP",
    },
}

# Stellar Expert base URLs for transaction/contract inspection.
STELLAR_EXPERT_URLS = {
    TESTNET: "https://stellar.expert/explorer/testnet",
    MAINNET: "https://stellar.expert/explorer/public",
}


def contract_address(name: str, network: str) -> str:
    """Return the deployed contract address for `name` on `network`.

    Raises ValueError if the address is still the un-deployed placeholder.
    """
    net = normalize_network(network)
    addr = CONTRACT_ADDRESSES[net].get(name)
    if not addr:
        raise ValueError(f"Unknown contract '{name}'. Known: {list(CONTRACT_ADDRESSES[net])}")
    if addr == "__PENDING__":
        raise ValueError(
            f"Contract '{name}' has not been deployed to {net} yet. "
            f"Deploy from build/{name}.wasm and update CONTRACT_ADDRESSES in constants.py."
        )
    return addr


# Backward-compatible aliases (testnet defaults); prefer contract_address() for
# network-aware lookups.
HIVEMIND_REGISTRY_ADDRESS = CONTRACT_ADDRESSES[TESTNET]["hive_registry"]
MEMORY_ANCHOR_ADDRESS = CONTRACT_ADDRESSES[TESTNET]["memory_anchor"]

SOROBAN_RPC_URLS = {
    TESTNET: "https://soroban-testnet.stellar.org",
    MAINNET: "https://mainnet.sorobanrpc.com",
}

HORIZON_URLS = {
    TESTNET: "https://horizon-testnet.stellar.org",
    MAINNET: "https://horizon.stellar.org",
}

NETWORK_PASSPHRASES = {
    TESTNET: "Test SDF Network ; September 2015",
    MAINNET: "Public Global Stellar Network ; September 2015",
}

# Friendbot funds brand-new testnet accounts with lumens (testnet only).
FRIENDBOT_URL = "https://friendbot.stellar.org"

# ─── Hosted compile service ──────────────────────────────────────────────────
# `mycelium compile` POSTs source here when no local Rust/stellar-cli toolchain
# is present, so a new user can compile to WASM with zero local install. Points
# at the live IDE backend's `/compile` endpoint (runs the compiler in Docker).
# Self-hosters override via the MYCELIUM_COMPILE_URL env var.
import os as _os

DEFAULT_COMPILE_URL = "https://mycelium-zgez.onrender.com/compile"
COMPILE_URL = _os.environ.get("MYCELIUM_COMPILE_URL", DEFAULT_COMPILE_URL)

# Hosted off-chain indexer (O(1) agent/job discovery). Mirrors the compile-URL
# convention: the SDK/CLI default to the hosted Mycelium indexer and fall back to
# the on-chain event-scan when it is unreachable. Self-hosters override via the
# MYCELIUM_INDEXER_URL env var (point it at their own `indexer.api` deployment).
DEFAULT_INDEXER_URL = "https://mycelium-indexer.onrender.com"
INDEXER_URL = _os.environ.get("MYCELIUM_INDEXER_URL", DEFAULT_INDEXER_URL)

# Minimum balance (XLM) required to attempt a mainnet deployment, per sdk.md.
MAINNET_MIN_XLM = 5.0

# ─── Protocol fee (the business model) ───────────────────────────────────────
# The take-rate skimmed to the Mycelium treasury when an escrow releases a bounty
# on a passing verdict (see escrow_contract.py). Basis points: 250 = 2.5%. The
# escrow hard-caps this at MAX_FEE_BPS (10%) on-chain regardless of what is passed.
#
# Defaults to 0 (fee OFF) so testnet demos and existing tests keep paying the
# worker 100%. Turn revenue on for a deployment by setting BOTH env vars:
#   MYCELIUM_FEE_BPS=250
#   MYCELIUM_FEE_COLLECTOR=G... (the treasury account that receives the fee)
# Refunds never pay a fee; the cut is only taken on a successful release.
# Default protocol fee collectors per network.
FEE_COLLECTORS = {
    TESTNET: "GCKYLSBT7VE5XW326LCGV72TZRYDX5WIX3TKCE74GU4WBTVSVUDBPAYR",
    MAINNET: "GCT7GPSGA4OQXCN6KYUVDCZY2P4D4QHA5GCPC72XYEN3RRF36NR6D2XX",
}


def protocol_fee_collector(network: str) -> str | None:
    """Return the fee collector address for the network, overridden by env var."""
    env_val = _os.environ.get("MYCELIUM_FEE_COLLECTOR")
    if env_val:
        return env_val
    return FEE_COLLECTORS.get(normalize_network(network))


MAX_FEE_BPS = 1000  # mirrors escrow_contract.MAX_FEE_BPS — reject early + clearly
PROTOCOL_FEE_BPS = int(_os.environ.get("MYCELIUM_FEE_BPS", "0"))
PROTOCOL_FEE_COLLECTOR = _os.environ.get("MYCELIUM_FEE_COLLECTOR", "") or FEE_COLLECTORS[TESTNET]

# ─── Native asset (XLM) Stellar Asset Contract ───────────────────────────────
# The SAC address for native XLM is deterministic per network (it is the
# contract id of the native asset). Used as the default `token` for x402 escrow
# settlements when the caller does not pass an explicit token contract.
NATIVE_SAC_ADDRESSES = {
    TESTNET: "CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC",
    MAINNET: "CAS3J7GYLGXMF6TDJBBYYSE3HQ6BBSMLNUQ34T6TZMYMW2EVH34XOWMA",
}


def native_token_address(network: str) -> str:
    """Return the native-XLM Stellar Asset Contract address for `network`."""
    return NATIVE_SAC_ADDRESSES[normalize_network(network)]


def normalize_network(network: str) -> str:
    """Lower-case and validate a network name, returning 'testnet' or 'mainnet'."""
    net = (network or "").lower()
    if net not in (TESTNET, MAINNET):
        raise ValueError(f"Unknown network '{network}'. Expected 'testnet' or 'mainnet'.")
    return net
