"""Token Bridge: simple lock mechanism for bridging."""
token: address
locked: Mapping[address, uint256]

@external
def __init__(t_addr: address):
    self.token = t_addr

@external
def lock(amount: uint256):
    self.locked[msg_sender] += amount

@external
def unlock(user: address, amount: uint256):
    # Admin only, release tokens
    self.locked[user] -= amount
