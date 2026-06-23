"""
Network and registry constants — the single source of truth shared by
`AgentContext` (SDK) and the `mycelium deploy` / `mycelium register` CLI commands.

Values mirror the proven configuration used by the IDE backend's `/api/deploy`
endpoint (ide/backend/main.py).
"""

# ─── Hive Registry ───────────────────────────────────────────────────────────
# Global on-chain registry that maps unique agent names -> {address, capability,
# endpoint, reputation}. Built by compiling `hive_registry.py` (repo root) with
# our own compiler and deploying it. The value below is the live Soroban
# **testnet** deployment. Projects may override it per deployment via
# `[registry].hive_registry_address` in mycelium.toml (e.g. for mainnet).
HIVEMIND_REGISTRY_ADDRESS = "CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC"

# ─── Stellar / Soroban network endpoints ─────────────────────────────────────
TESTNET = "testnet"
MAINNET = "mainnet"

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

# Minimum balance (XLM) required to attempt a mainnet deployment, per sdk.md.
MAINNET_MIN_XLM = 5.0

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
