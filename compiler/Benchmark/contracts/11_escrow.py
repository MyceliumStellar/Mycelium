"""Escrow: trustless payment release by arbiter."""
owner: address
payer: address
payee: address
arbiter: address
amount: uint256
released: bool
refunded: bool

@event
class Released:
    to: indexed(address)
    amount: uint256

@event
class Refunded:
    to: indexed(address)
    amount: uint256

@external
def __init__(payee: address, arbiter: address):
    self.payer = msg_sender
    self.payee = payee
    self.arbiter = arbiter
    self.amount = msg_value
    self.released = False
    self.refunded = False

@external
def release():
    assert(msg_sender == self.arbiter, "Not arbiter")
    assert(not self.released, "Already released")
    assert(not self.refunded, "Already refunded")
    self.released = True
    self.Released(self.payee, self.amount)

@external
def refund():
    assert(msg_sender == self.arbiter, "Not arbiter")
    assert(not self.released, "Already released")
    assert(not self.refunded, "Already refunded")
    self.refunded = True
    self.Refunded(self.payer, self.amount)

@external
@view
def status() -> bool:
    return self.released
