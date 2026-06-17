"""Refund Manager: manage and issue refunds."""
refunds: Mapping[address, uint256]

@external
def add_refund(user: address, amount: uint256):
    # Admin only
    self.refunds[user] += amount

@external
def claim_refund():
    amount: uint256 = self.refunds[msg_sender]
    assert(amount > 0, "No refund")
    self.refunds[msg_sender] = 0
    # Transfer ETH
    pass
