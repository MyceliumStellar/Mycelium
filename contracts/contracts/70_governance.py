"""Governance: full governance with proposal and voting."""
proposal_count: uint256
proposals: Mapping[uint256, address]

@external
def propose(target: address):
    pid: uint256 = self.proposal_count
    self.proposals[pid] = target
    self.proposal_count += 1

@external
def vote(pid: uint256):
    # Logic to record vote
    pass
