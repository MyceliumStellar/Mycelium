"""Staking: stake tokens and earn rewards."""
owner: address
stake_token: address
reward_rate: uint256
total_staked: uint256
stakes: Mapping[address, uint256]
stake_times: Mapping[address, uint256]
rewards: Mapping[address, uint256]

@event
class Staked:
    user: indexed(address)
    amount: uint256

@event
class Unstaked:
    user: indexed(address)
    amount: uint256

@event
class RewardClaimed:
    user: indexed(address)
    reward: uint256

@external
def __init__(rate: uint256):
    self.owner = msg_sender
    self.reward_rate = rate
    self.total_staked = 0

@external
def stake(amount: uint256):
    assert(amount > 0, "Zero amount")
    self.stakes[msg_sender] += amount
    self.stake_times[msg_sender] = block_timestamp
    self.total_staked += amount
    self.Staked(msg_sender, amount)

@external
def unstake(amount: uint256):
    assert(self.stakes[msg_sender] >= amount, "Insufficient stake")
    elapsed: uint256 = block_timestamp - self.stake_times[msg_sender]
    pending: uint256 = (self.stakes[msg_sender] * self.reward_rate * elapsed) / 1000000
    self.rewards[msg_sender] += pending
    self.stakes[msg_sender] -= amount
    self.total_staked -= amount
    self.Unstaked(msg_sender, amount)

@external
def claim_reward():
    reward: uint256 = self.rewards[msg_sender]
    assert(reward > 0, "No reward")
    self.rewards[msg_sender] = 0
    self.RewardClaimed(msg_sender, reward)

@external
@view
def pending_reward(user: address) -> uint256:
    elapsed: uint256 = block_timestamp - self.stake_times[user]
    return (self.stakes[user] * self.reward_rate * elapsed) / 1000000

@external
@view
def staked_amount(user: address) -> uint256:
    return self.stakes[user]
