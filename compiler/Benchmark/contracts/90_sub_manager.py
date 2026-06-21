"""Subscription Manager: manage subscription plans."""
owner: address
plans: Mapping[uint256, uint256]

@external
def __init__():
    self.owner = msg_sender

@external
def set_plan_price(id: uint256, price: uint256):
    assert(msg_sender == self.owner, "Not owner")
    self.plans[id] = price
