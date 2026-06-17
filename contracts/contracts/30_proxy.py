"""Upgradeable Proxy: delegate calls to an implementation."""
owner: address
implementation: address

@event
class Upgraded:
    implementation: indexed(address)

@external
def __init__(impl: address):
    self.owner = msg_sender
    self.implementation = impl

@external
def upgrade(new_impl: address):
    assert(msg_sender == self.owner, "Not owner")
    self.implementation = new_impl
    self.Upgraded(new_impl)

@external
@view
def get_implementation() -> address:
    return self.implementation
