"""Vault: deposit and withdraw ETH."""
balances: Mapping[address, uint256]

@event
class Deposited:
    user: indexed(address)
    amount: uint256

@event
class Withdrawn:
    user: indexed(address)
    amount: uint256

@external
def deposit():
    self.balances[msg_sender] += msg_value
    self.Deposited(msg_sender, msg_value)

@external
def withdraw(amount: uint256):
    assert(self.balances[msg_sender] >= amount, "Insufficient")
    self.balances[msg_sender] -= amount
    self.Withdrawn(msg_sender, amount)

@external
@view
def balance_of(user: address) -> uint256:
    return self.balances[user]
