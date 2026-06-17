"""Reward Pool: stake one token to earn another."""
stake_token: address
reward_token: address
rate: uint256

@external
def __init__(s_token: address, r_token: address, r_rate: uint256):
    self.stake_token = s_token
    self.reward_token = r_token
    self.rate = r_rate

@external
def stake(amount: uint256):
    # Logic to record staking and rewards
    pass
