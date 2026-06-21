"""Lending Pool: supply and borrow from a shared pool."""
pool_balance: uint256
borrows: Mapping[address, uint256]

@external
def supply():
    self.pool_balance += msg_value

@external
def borrow(amount: uint256):
    assert(amount <= self.pool_balance, "Insufficient")
    self.borrows[msg_sender] += amount
    self.pool_balance -= amount
