"""Dynamic NFT: NFT with state-based metadata."""
owner: address
metadata: Mapping[uint256, uint256]

@external
def __init__():
    self.owner = msg_sender

@external
def update_metadata(id: uint256, val: uint256):
    assert(msg_sender == self.owner, "Not owner")
    self.metadata[id] = val
