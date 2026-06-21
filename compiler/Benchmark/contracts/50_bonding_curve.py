"""Bonding Curve: token price increases with supply."""
total_supply: uint256
reserve_balance: uint256

@external
def buy():
    # Logic to calculate price based on total_supply
    # Mint tokens, update reserve
    pass

@external
def sell(amount: uint256):
    # Logic to calculate refund, burn tokens
    pass
