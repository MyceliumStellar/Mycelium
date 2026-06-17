"""Liquidity Locker: lock LP tokens for a period."""
lp_token: address
locks: Mapping[address, uint256]
unlock_times: Mapping[address, uint256]

@external
def __init__(lp: address):
    self.lp_token = lp

@external
def lock(amount: uint256, duration: uint256):
    self.locks[msg_sender] += amount
    self.unlock_times[msg_sender] = block_timestamp + duration

@external
def unlock():
    assert(block_timestamp >= self.unlock_times[msg_sender], "Locked")
    # Transfer LP tokens back
    pass

@external
@view
def get_lock_info(user: address) -> (uint256, uint256):
    return (self.locks[user], self.unlock_times[user])
