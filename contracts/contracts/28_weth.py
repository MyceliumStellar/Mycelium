"""Wrapped ETH: wrap ETH into an ERC-20 token."""
name: String
symbol: String
decimals: uint256
total_supply: uint256
balances: Mapping[address, uint256]

@event
class Deposit:
    user: indexed(address)
    amount: uint256

@event
class Withdrawal:
    user: indexed(address)
    amount: uint256

@external
def __init__():
    self.name = "Wrapped Ether"
    self.symbol = "WETH"
    self.decimals = 18

@external
def deposit():
    self.balances[msg_sender] += msg_value
    self.total_supply += msg_value
    self.Deposit(msg_sender, msg_value)

@external
def withdraw(amount: uint256):
    assert(self.balances[msg_sender] >= amount, "Insufficient balance")
    self.balances[msg_sender] -= amount
    self.total_supply -= amount
    self.Withdrawal(msg_sender, amount)

@external
@view
def balance_of(account: address) -> uint256:
    return self.balances[account]
