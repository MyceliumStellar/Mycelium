"""Liquidity Mine: earn rewards for providing liquidity."""
reward_token: address
lp_staked: Mapping[address, uint256]

@external
def __init__(r_token: address):
    self.reward_token = r_token

@external
def stake_lp(amount: uint256):
    self.lp_staked[msg_sender] += amount

@external
def withdraw_lp(amount: uint256):
    self.lp_staked[msg_sender] -= amount
