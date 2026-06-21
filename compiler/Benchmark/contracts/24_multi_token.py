"""Multi-token: manage multiple token types in one contract."""
owner: address
balances: Mapping[uint256, Mapping[address, uint256]]

@event
class TransferSingle:
    operator: indexed(address)
    from_addr: indexed(address)
    to_addr: indexed(address)
    id: uint256
    value: uint256

@external
def __init__():
    self.owner = msg_sender

@external
def mint(to: address, id: uint256, amount: uint256):
    assert(msg_sender == self.owner, "Not owner")
    self.balances[id][to] += amount
    self.TransferSingle(msg_sender, ZERO_ADDRESS, to, id, amount)

@external
def safe_transfer_from(from_addr: address, to_addr: address, id: uint256, amount: uint256):
    assert(from_addr == msg_sender, "Not authorized")
    assert(self.balances[id][from_addr] >= amount, "Insufficient balance")
    self.balances[id][from_addr] -= amount
    self.balances[id][to_addr] += amount
    self.TransferSingle(msg_sender, from_addr, to_addr, id, amount)

@external
@view
def balance_of(account: address, id: uint256) -> uint256:
    return self.balances[id][account]
