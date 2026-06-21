"""Bridge: lock tokens for cross-chain transfer."""
token: address
admin: address
locked_balances: Mapping[address, uint256]

@event
class Locked:
    user: indexed(address)
    amount: uint256
    dest_chain: uint256

@external
def __init__(token_addr: address):
    self.token = token_addr
    self.admin = msg_sender

@external
def bridge(amount: uint256, chain_id: uint256):
    self.locked_balances[msg_sender] += amount
    self.Locked(msg_sender, amount, chain_id)

@external
def release(user: address, amount: uint256):
    assert(msg_sender == self.admin, "Not admin")
    self.locked_balances[user] -= amount

@external
@view
def get_locked(user: address) -> uint256:
    return self.locked_balances[user]
