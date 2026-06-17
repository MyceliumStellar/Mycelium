"""Simple Storage: store and retrieve a uint256 value."""
stored_value: uint256
owner: address

@external
def __init__():
    self.owner = msg_sender
    self.stored_value = 0

@external
def set(value: uint256):
    self.stored_value = value

@external
@view
def get() -> uint256:
    return self.stored_value
