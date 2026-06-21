"""Crowdfund: raise ETH with a goal and deadline."""
owner: address
goal: uint256
deadline: uint256
total_raised: uint256
contributions: Mapping[address, uint256]
goal_reached: bool
finalized: bool

@event
class Contributed:
    contributor: indexed(address)
    amount: uint256

@event
class GoalReached:
    total: uint256

@event
class Refunded:
    contributor: indexed(address)
    amount: uint256

@external
def __init__(goal: uint256, duration: uint256):
    self.owner = msg_sender
    self.goal = goal
    self.deadline = block_timestamp + duration
    self.total_raised = 0
    self.goal_reached = False
    self.finalized = False

@external
def contribute():
    assert(block_timestamp < self.deadline, "Campaign ended")
    assert(msg_value > 0, "Zero contribution")
    self.contributions[msg_sender] += msg_value
    self.total_raised += msg_value
    self.Contributed(msg_sender, msg_value)
    if self.total_raised >= self.goal:
        self.goal_reached = True
        self.GoalReached(self.total_raised)

@external
def refund():
    assert(block_timestamp >= self.deadline, "Not ended")
    assert(not self.goal_reached, "Goal was reached")
    amount: uint256 = self.contributions[msg_sender]
    assert(amount > 0, "Nothing to refund")
    self.contributions[msg_sender] = 0
    self.Refunded(msg_sender, amount)

@external
def finalize():
    assert(msg_sender == self.owner, "Not owner")
    assert(self.goal_reached, "Goal not reached")
    assert(not self.finalized, "Already finalized")
    self.finalized = True

@external
@view
def get_total_raised() -> uint256:
    return self.total_raised
