"""CLI subcommands package."""

def resolve_network(
    network: str | None,
    use_testnet: bool = False,
    use_mainnet: bool = False,
) -> str | None:
    """Resolve the effective network from the three flag sources.

    Priority: --mainnet/-m > --testnet/-t > --network/-n value.
    Returns None if no flag was explicitly set (so the caller can fall
    back to mycelium.toml).
    """
    if use_mainnet:
        return "mainnet"
    if use_testnet:
        return "testnet"
    return network
