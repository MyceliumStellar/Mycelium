"""Stablecoin: simple collateral-backed stable asset."""
collateral_token: address
debt: Mapping[address, uint256]
collateral: Mapping[address, uint256]

@external
def __init__(collat: address):
    self.collateral_token = collat

@external
def mint(amount: uint256):
    # Simplified logic: 150% collateral required
    required: uint256 = (amount * 150) / 100
    assert(self.collateral[msg_sender] >= required, "Insufficient collateral")
    self.debt[msg_sender] += amount

@external
def deposit_collateral():
    self.collateral[msg_sender] += msg_value
