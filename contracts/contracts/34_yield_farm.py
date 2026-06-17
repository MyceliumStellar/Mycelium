"""Yield Farm: distribute rewards to LP stakers."""
lp_token: address
reward_token: address
reward_per_block: uint256
total_staked: uint256
stakers: Mapping[address, uint256]

@external
def __init__(lp: address, reward: address, rate: uint256):
    self.lp_token = lp
    self.reward_token = reward
    self.reward_per_block = rate

@external
def deposit(amount: uint256):
    self.stakers[msg_sender] += amount
    self.total_staked += amount

@external
def harvest():
    # Logic to calculate and transfer rewards
    pass

@external
@view
def get_staked(user: address) -> uint256:
    return self.stakers[user]
