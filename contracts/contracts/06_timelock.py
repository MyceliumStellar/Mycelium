"""Timelock: schedule and execute calls after a delay."""
owner: address
delay: uint256
queued: Mapping[uint256, bool]
queue_times: Mapping[uint256, uint256]
tx_count: uint256

@event
class Queued:
    tx_id: indexed(uint256)
    execute_at: uint256

@event
class Executed:
    tx_id: indexed(uint256)

@external
def __init__(delay: uint256):
    self.owner = msg_sender
    self.delay = delay

@external
def queue() -> uint256:
    assert(msg_sender == self.owner, "Not owner")
    tx_id: uint256 = self.tx_count
    execute_at: uint256 = block_timestamp + self.delay
    self.queued[tx_id] = True
    self.queue_times[tx_id] = execute_at
    self.tx_count += 1
    self.Queued(tx_id, execute_at)
    return tx_id

@external
def execute(tx_id: uint256):
    assert(msg_sender == self.owner, "Not owner")
    assert(self.queued[tx_id], "Not queued")
    assert(block_timestamp >= self.queue_times[tx_id], "Too early")
    self.queued[tx_id] = False
    self.Executed(tx_id)

@external
def cancel(tx_id: uint256):
    assert(msg_sender == self.owner, "Not owner")
    assert(self.queued[tx_id], "Not queued")
    self.queued[tx_id] = False

@external
@view
def is_queued(tx_id: uint256) -> bool:
    return self.queued[tx_id]
