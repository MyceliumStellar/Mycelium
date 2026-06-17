"""Fixed Token: token with fixed supply."""
total_supply: uint256
balances: Mapping[address, uint256]

@external
def __init__(supply: uint256):
    self.total_supply = supply
    self.balances[msg_sender] = supply

@external
@view
def get_balance(account: address) -> uint256:
    return self.balances[account]
