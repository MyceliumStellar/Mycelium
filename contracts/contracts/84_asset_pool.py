"""Lending Asset Pool: asset specific pool for lending."""
asset: address
total_supplied: uint256

@external
def __init__(asset_addr: address):
    self.asset = asset_addr
    self.total_supplied = 0

@external
def supply(amount: uint256):
    self.total_supplied += amount
