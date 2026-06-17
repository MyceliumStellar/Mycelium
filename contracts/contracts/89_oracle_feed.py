"""Oracle Feed: single source data feed."""
owner: address
price: uint256

@external
def __init__():
    self.owner = msg_sender

@external
def set_price(v: uint256):
    assert(msg_sender == self.owner, "Not owner")
    self.price = v

@external
@view
def get_price() -> uint256:
    return self.price
