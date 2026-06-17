"""Simple Escrow: trustless payment for services."""
buyer: address
seller: address
agent: address
amount: uint256
is_released: bool

@external
def __init__(seller: address, agent: address):
    self.buyer = msg_sender
    self.seller = seller
    self.agent = agent
    self.amount = msg_value
    self.is_released = False

@external
def release():
    assert(msg_sender == self.buyer or msg_sender == self.agent, "Not authorized")
    self.is_released = True

@external
def refund():
    assert(msg_sender == self.seller or msg_sender == self.agent, "Not authorized")
    self.is_released = False

@external
@view
def get_status() -> bool:
    return self.is_released
