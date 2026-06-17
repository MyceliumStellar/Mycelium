"""Basic Multi-sig: 2-of-3 multisig wallet."""
owners: DynArray[address, 3]
approvals: Mapping[uint256, uint256]

@external
def __init__(owner_list: DynArray[address, 3]):
    self.owners = owner_list

@external
def approve(tx_id: uint256):
    self.approvals[tx_id] += 1
    if self.approvals[tx_id] >= 2:
        # Execute tx
        pass
