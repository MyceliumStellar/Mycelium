"""Refund Crowdsale: refund users if goal not reached."""
goal: uint256
deadline: uint256
contributions: Mapping[address, uint256]
total_raised: uint256

@external
def contribute():
    assert(block_timestamp < self.deadline, "Ended")
    self.contributions[msg_sender] += msg_value
    self.total_raised += msg_value

@external
def claim_refund():
    assert(block_timestamp >= self.deadline, "Ongoing")
    assert(self.total_raised < self.goal, "Goal met")
    # Return ETH
    pass
