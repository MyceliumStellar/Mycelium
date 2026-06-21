"""Lending: borrow tokens using ETH collateral."""
owner: address
interest_rate: uint256
collateral_ratio: uint256
borrows: Mapping[address, uint256]
collateral: Mapping[address, uint256]

@event
class Borrowed:
    user: indexed(address)
    amount: uint256
    collateral: uint256

@event
class Repaid:
    user: indexed(address)
    amount: uint256

@external
def __init__(rate: uint256, ratio: uint256):
    self.owner = msg_sender
    self.interest_rate = rate
    self.collateral_ratio = ratio

@external
def borrow(amount: uint256):
    required_collateral: uint256 = (amount * self.collateral_ratio) / 100
    assert(msg_value >= required_collateral, "Insufficient collateral")
    self.borrows[msg_sender] += amount
    self.collateral[msg_sender] += msg_value
    self.Borrowed(msg_sender, amount, msg_value)

@external
def repay(amount: uint256):
    assert(self.borrows[msg_sender] >= amount, "Repaying too much")
    self.borrows[msg_sender] -= amount
    if self.borrows[msg_sender] == 0:
        collateral_to_return: uint256 = self.collateral[msg_sender]
        self.collateral[msg_sender] = 0
    self.Repaid(msg_sender, amount)

@external
@view
def get_borrow(user: address) -> uint256:
    return self.borrows[user]

@external
@view
def get_collateral(user: address) -> uint256:
    return self.collateral[user]
