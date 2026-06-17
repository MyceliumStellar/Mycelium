"""NFT Royalty: basic NFT with royalty support."""
owner: address
royalty_percent: uint256

@external
def __init__(percent: uint256):
    self.owner = msg_sender
    self.royalty_percent = percent

@external
def pay_royalty(amount: uint256):
    royalty: uint256 = (amount * self.royalty_percent) / 100
    # Transfer royalty to owner
    pass
