"""Ownable: transfer and renounce ownership."""
owner: address

@event
class OwnershipTransferred:
    prev: indexed(address)
    next: indexed(address)

@external
def __init__():
    self.owner = msg_sender

@external
def transfer_ownership(new_owner: address):
    assert(msg_sender == self.owner, "Not owner")
    assert(new_owner != ZERO_ADDRESS, "Zero address")
    self.OwnershipTransferred(self.owner, new_owner)
    self.owner = new_owner

@external
def renounce_ownership():
    assert(msg_sender == self.owner, "Not owner")
    self.OwnershipTransferred(self.owner, ZERO_ADDRESS)
    self.owner = ZERO_ADDRESS

@external
@view
def get_owner() -> address:
    return self.owner
