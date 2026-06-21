"""Governance Token: token with voting power based on balance."""
name: String
symbol: String
decimals: uint256
total_supply: uint256
balances: Mapping[address, uint256]
delegates: Mapping[address, address]

@event
class DelegateChanged:
    delegator: indexed(address)
    from_delegate: indexed(address)
    to_delegate: indexed(address)

@external
def __init__(name: String, symbol: String, supply: uint256):
    self.name = name
    self.symbol = symbol
    self.decimals = 18
    self.total_supply = supply
    self.balances[msg_sender] = supply

@external
def delegate(delegatee: address):
    current_delegate: address = self.delegates[msg_sender]
    self.delegates[msg_sender] = delegatee
    self.DelegateChanged(msg_sender, current_delegate, delegatee)

@external
@view
def get_votes(account: address) -> uint256:
    # Simplified: returns balance if self-delegated
    if self.delegates[account] == account:
        return self.balances[account]
    return 0

@external
@view
def balance_of(account: address) -> uint256:
    return self.balances[account]
