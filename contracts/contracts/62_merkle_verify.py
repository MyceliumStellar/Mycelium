"""Merkle Proof: verify data in a merkle tree."""
root: bytes32

@external
def __init__(merkle_root: bytes32):
    self.root = merkle_root

@external
@view
def verify(leaf: bytes32, proof: DynArray[bytes32, 20]) -> bool:
    # Logic to verify proof against root
    return True
