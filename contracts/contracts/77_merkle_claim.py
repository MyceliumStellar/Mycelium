"""Merkle Claim: claim tokens using a merkle proof."""
merkle_root: bytes32
claimed: Mapping[address, bool]

@external
def __init__(root: bytes32):
    self.merkle_root = root

@external
def claim(proof: DynArray[bytes32, 20]):
    assert(not self.claimed[msg_sender], "Already claimed")
    # Proof verification logic
    self.claimed[msg_sender] = True
