"""Shared NFT: fractional ownership of an NFT."""
nft_id: uint256
shares: Mapping[address, uint256]

@external
def __init__(id: uint256):
    self.nft_id = id

@external
def buy_shares():
    # Record shares based on payment
    pass
