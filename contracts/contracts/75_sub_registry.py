"""Subscription Registry: manage subscription status."""
active_until: Mapping[address, uint256]

@external
def renew(duration: uint256):
    self.active_until[msg_sender] = block_timestamp + duration

@external
@view
def is_active(user: address) -> bool:
    return self.active_until[user] > block_timestamp
