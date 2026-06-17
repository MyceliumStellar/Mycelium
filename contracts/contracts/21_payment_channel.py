"""Payment Channel: off-chain transfers with on-chain settlement."""
sender: address
receiver: address
expiration: uint256

@external
def __init__(receiver: address, duration: uint256):
    self.sender = msg_sender
    self.receiver = receiver
    self.expiration = block_timestamp + duration

@external
def close(amount: uint256):
    assert(msg_sender == self.receiver, "Not receiver")
    assert(amount <= self_balance, "Insufficient balance")
    # In a real scenario, this would verify a signature
    self.receiver = ZERO_ADDRESS # Dummy close logic

@external
def extend(duration: uint256):
    assert(msg_sender == self.sender, "Not sender")
    self.expiration += duration

@external
def claim_timeout():
    assert(block_timestamp >= self.expiration, "Not expired")
    # Return funds to sender
    pass

@external
@view
def get_expiration() -> uint256:
    return self.expiration
