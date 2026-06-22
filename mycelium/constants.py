"""
Re-export of SDK constants under the `mycelium` namespace so that
`from mycelium.constants import HIVEMIND_REGISTRY_ADDRESS` (as used in sdk.md
examples) resolves. The canonical definitions live in `mycelium_sdk.constants`.
"""

from mycelium_sdk.constants import (  # noqa: F401
    HIVEMIND_REGISTRY_ADDRESS,
    SOROBAN_RPC_URLS,
    HORIZON_URLS,
    NETWORK_PASSPHRASES,
    FRIENDBOT_URL,
    MAINNET_MIN_XLM,
    normalize_network,
)
