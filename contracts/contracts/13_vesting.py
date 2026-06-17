"""Vesting: linear token vesting with cliff."""
owner: address
beneficiary: address
total_amount: uint256
released: uint256
start_time: uint256
cliff: uint256
duration: uint256

@event
class Released:
    amount: uint256
    to: indexed(address)

@external
def __init__(beneficiary: address, total: uint256, cliff: uint256, duration: uint256):
    self.owner = msg_sender
    self.beneficiary = beneficiary
    self.total_amount = total
    self.released = 0
    self.start_time = block_timestamp
    self.cliff = cliff
    self.duration = duration

@external
def release():
    assert(block_timestamp >= self.start_time + self.cliff, "Cliff not reached")
    vested: uint256 = self._vested_amount()
    releasable: uint256 = vested - self.released
    assert(releasable > 0, "Nothing to release")
    self.released += releasable
    self.Released(releasable, self.beneficiary)

@external
@view
def _vested_amount() -> uint256:
    elapsed: uint256 = block_timestamp - self.start_time
    if elapsed >= self.duration:
        return self.total_amount
    return (self.total_amount * elapsed) / self.duration

@external
@view
def releasable() -> uint256:
    return self._vested_amount() - self.released
