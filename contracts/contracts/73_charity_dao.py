"""Charity DAO: vote on where to send donations."""
total_funds: uint256
charity_votes: Mapping[address, uint256]

@external
def donate():
    self.total_funds += msg_value

@external
def vote(charity: address):
    self.charity_votes[charity] += 1
