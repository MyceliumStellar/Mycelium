"""Token Timelock: lock tokens until a release date."""
token: address
beneficiary: address
release_time: uint256

@external
def __init__(t_addr: address, b_addr: address, r_time: uint256):
    self.token = t_addr
    self.beneficiary = b_addr
    self.release_time = r_time

@external
def release():
    assert(block_timestamp >= self.release_time, "Not yet")
    # Transfer tokens to beneficiary
    pass
