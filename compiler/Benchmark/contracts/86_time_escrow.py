"""Time Escrow: release funds after a certain time."""
payee: address
unlock_time: uint256

@external
def __init__(payee_addr: address, duration: uint256):
    self.payee = payee_addr
    self.unlock_time = block_timestamp + duration

@external
def release():
    assert(block_timestamp >= self.unlock_time, "Locked")
    # Transfer ETH to payee
    pass
