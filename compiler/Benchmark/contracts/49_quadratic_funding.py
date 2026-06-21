"""Quadratic Funding: matching pool for projects."""
matching_pool: uint256
project_contributions: Mapping[uint256, uint256]
project_squares: Mapping[uint256, uint256]

@external
def __init__():
    self.matching_pool = 0

@external
def donate(project_id: uint256):
    amount: uint256 = msg_value
    self.project_contributions[project_id] += amount
    # sqrt logic for matching
    pass
