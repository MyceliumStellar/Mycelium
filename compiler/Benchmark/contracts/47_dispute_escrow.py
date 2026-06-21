"""Dispute Escrow: escrow with an arbiter for disputes."""
buyer: address
seller: address
arbiter: address
amount: uint256
disputed: bool

@external
def __init__(seller: address, arbiter: address):
    self.buyer = msg_sender
    self.seller = seller
    self.arbiter = arbiter
    self.amount = msg_value
    self.disputed = False

@external
def raise_dispute():
    assert(msg_sender == self.buyer or msg_sender == self.seller, "Not party")
    self.disputed = True

@external
def resolve(to_seller: bool):
    assert(msg_sender == self.arbiter, "Not arbiter")
    # Transfer funds accordingly
    pass
