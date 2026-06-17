"""Splitter: split incoming ETH among multiple recipients."""
recipients: Mapping[uint256, address]
shares: Mapping[uint256, uint256]
count: uint256
total_shares: uint256

@event
class Split:
    to: indexed(address)
    amount: uint256

@external
def __init__(addresses: DynArray[address, 10], portions: DynArray[uint256, 10]):
    self.count = 0
    self.total_shares = 0
    # Manual loop to set recipients and shares
    # Note: loop logic would be in _transform_statement
    pass

@external
def distribute():
    amount: uint256 = msg_value
    assert(amount > 0, "No ETH")
    # Logic to split msg_value based on shares
    pass

@external
@view
def get_share(idx: uint256) -> uint256:
    return self.shares[idx]
