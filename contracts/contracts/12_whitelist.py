"""Whitelist: manage approved addresses."""
owner: address
whitelist: Mapping[address, bool]
count: uint256

@event
class Added:
    account: indexed(address)

@event
class Removed:
    account: indexed(address)

@external
def __init__():
    self.owner = msg_sender
    self.count = 0

@external
def add(account: address):
    assert(msg_sender == self.owner, "Not owner")
    assert(not self.whitelist[account], "Already whitelisted")
    self.whitelist[account] = True
    self.count += 1
    self.Added(account)

@external
def remove(account: address):
    assert(msg_sender == self.owner, "Not owner")
    assert(self.whitelist[account], "Not whitelisted")
    self.whitelist[account] = False
    self.count -= 1
    self.Removed(account)

@external
@view
def is_whitelisted(account: address) -> bool:
    return self.whitelist[account]

@external
@view
def total() -> uint256:
    return self.count
