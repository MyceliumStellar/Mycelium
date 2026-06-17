"""Donation: transparent charitable giving."""
charity: address
total_donated: uint256

@external
def __init__(charity_addr: address):
    self.charity = charity_addr

@external
def donate():
    self.total_donated += msg_value

@external
def withdraw():
    assert(msg_sender == self.charity, "Not charity")
    # Transfer funds to charity
    pass
