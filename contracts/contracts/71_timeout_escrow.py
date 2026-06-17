"""Timeout Escrow: escrow that reverts if not released."""
buyer: address
seller: address
deadline: uint256

@external
def __init__(seller_addr: address, duration: uint256):
    self.buyer = msg_sender
    self.seller = seller_addr
    self.deadline = block_timestamp + duration

@external
def refund():
    assert(block_timestamp >= self.deadline, "Ongoing")
    # Return ETH to buyer
    pass
