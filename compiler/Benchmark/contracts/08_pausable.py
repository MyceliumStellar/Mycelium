"""Pausable: pause and unpause contract operations."""
owner: address
paused: bool

@event
class Paused:
    account: indexed(address)

@event
class Unpaused:
    account: indexed(address)

@external
def __init__():
    self.owner = msg_sender
    self.paused = False

@external
def pause():
    assert(msg_sender == self.owner, "Not owner")
    assert(not self.paused, "Already paused")
    self.paused = True
    self.Paused(msg_sender)

@external
def unpause():
    assert(msg_sender == self.owner, "Not owner")
    assert(self.paused, "Not paused")
    self.paused = False
    self.Unpaused(msg_sender)

@external
@view
def is_paused() -> bool:
    return self.paused
