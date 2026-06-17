"""ERC-20 Token with mint, burn, transfer."""
name: String
symbol: String
decimals: uint256
total_supply: uint256
balances: Mapping[address, uint256]
allowances: Mapping[address, Mapping[address, uint256]]
owner: address

@event
class Transfer:
    sender: indexed(address)
    receiver: indexed(address)
    value: uint256

@event
class Approval:
    owner: indexed(address)
    spender: indexed(address)
    value: uint256

@external
def __init__(name: String, symbol: String, supply: uint256):
    self.name = name
    self.symbol = symbol
    self.decimals = 18
    self.owner = msg_sender
    self.total_supply = supply
    self.balances[msg_sender] = supply

@external
def transfer(to: address, amount: uint256) -> bool:
    assert(self.balances[msg_sender] >= amount, "Insufficient")
    self.balances[msg_sender] -= amount
    self.balances[to] += amount
    self.Transfer(msg_sender, to, amount)
    return True

@external
def approve(spender: address, amount: uint256) -> bool:
    self.allowances[msg_sender][spender] = amount
    self.Approval(msg_sender, spender, amount)
    return True

@external
def transfer_from(sender: address, to: address, amount: uint256) -> bool:
    assert(self.allowances[sender][msg_sender] >= amount, "Not allowed")
    assert(self.balances[sender] >= amount, "Insufficient")
    self.allowances[sender][msg_sender] -= amount
    self.balances[sender] -= amount
    self.balances[to] += amount
    self.Transfer(sender, to, amount)
    return True

@external
def mint(to: address, amount: uint256):
    assert(msg_sender == self.owner, "Not owner")
    self.balances[to] += amount
    self.total_supply += amount

@external
def burn(amount: uint256):
    assert(self.balances[msg_sender] >= amount, "Insufficient")
    self.balances[msg_sender] -= amount
    self.total_supply -= amount

@external
@view
def balance_of(account: address) -> uint256:
    return self.balances[account]

@external
@view
def allowance(own: address, spender: address) -> uint256:
    return self.allowances[own][spender]
