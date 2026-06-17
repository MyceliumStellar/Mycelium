"""Charity Pool: pool funds for a specific charity."""
charity: address
balance: uint256

@external
def __init__(charity_addr: address):
    self.charity = charity_addr
    self.balance = 0

@external
def donate():
    self.balance += msg_value

@external
def withdraw():
    assert(msg_sender == self.charity, "Not charity")
    # Transfer funds to charity
    pass
