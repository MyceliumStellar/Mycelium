"""Secret Game: commit-reveal secret number."""
commits: Mapping[address, bytes32]
reveals: Mapping[address, uint256]

@external
def commit(c: bytes32):
    self.commits[msg_sender] = c

@external
def reveal(n: uint256, salt: bytes32):
    # Verify hash(n, salt) == commits[msg_sender]
    self.reveals[msg_sender] = n
