"""Subscription: recurring payments for services."""
owner: address
plans: Mapping[uint256, uint256]
plan_count: uint256
subscriptions: Mapping[address, Mapping[uint256, uint256]]

@event
class Subscribed:
    user: indexed(address)
    plan_id: uint256
    expiration: uint256

@external
def __init__():
    self.owner = msg_sender
    self.plan_count = 0

@external
def add_plan(price: uint256) -> uint256:
    assert(msg_sender == self.owner, "Not owner")
    plan_id: uint256 = self.plan_count
    self.plans[plan_id] = price
    self.plan_count += 1
    return plan_id

@external
def subscribe(plan_id: uint256, duration: uint256):
    price: uint256 = self.plans[plan_id] * duration
    assert(msg_value >= price, "Insufficient payment")
    expiration: uint256 = block_timestamp + (duration * 30 * 86400) # Monthly
    self.subscriptions[msg_sender][plan_id] = expiration
    self.Subscribed(msg_sender, plan_id, expiration)

@external
@view
def is_active(user: address, plan_id: uint256) -> bool:
    return self.subscriptions[user][plan_id] > block_timestamp
