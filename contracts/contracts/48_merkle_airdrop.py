"""Merkle Airdrop: claim tokens using merkle proof."""
root: bytes32
token: address
claimed: Mapping[address, bool]

@external
def __init__(token_addr: address, merkle_root: bytes32):
    self.token = token_addr
    self.root = merkle_root

@external
def claim(amount: uint256, proof: DynArray[bytes32, 20]):
    assert(not self.claimed[msg_sender], "Claimed")
    # Logic to verify merkle proof
    self.claimed[msg_sender] = True
