"""Shared Wallet: multiple owners with budget."""
owners: Mapping[address, bool]
budget: uint256
spent: uint256

@external
def __init__(budget: uint256):
    self.owners[msg_sender] = True
    self.budget = budget
    self.spent = 0

@external
def spend(amount: uint256):
    assert(self.owners[msg_sender], "Not owner")
    assert(self.spent + amount <= self.budget, "Over budget")
    self.spent += amount
