"""Voting: multi-option poll."""
options: Mapping[uint256, uint256]
voters: Mapping[address, bool]
option_count: uint256

@external
def __init__(count: uint256):
    self.option_count = count

@external
def vote(idx: uint256):
    assert(not self.voters[msg_sender], "Voted")
    assert(idx < self.option_count, "Invalid option")
    self.options[idx] += 1
    self.voters[msg_sender] = True

@external
@view
def get_count(idx: uint256) -> uint256:
    return self.options[idx]
