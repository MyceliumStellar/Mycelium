"""Fractional NFT: share ownership of an NFT."""
nft_token: address
nft_id: uint256
total_shares: uint256
shares: Mapping[address, uint256]

@external
def __init__(token: address, id: uint256, supply: uint256):
    self.nft_token = token
    self.nft_id = id
    self.total_shares = supply
    self.shares[msg_sender] = supply

@external
def transfer_shares(to: address, amount: uint256):
    self.shares[msg_sender] -= amount
    self.shares[to] += amount
