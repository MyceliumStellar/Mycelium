"""Bridge Receiver: receiver for tokens bridged from another chain."""
admin: address
processed_txs: Mapping[bytes32, bool]

@external
def __init__():
    self.admin = msg_sender

@external
def mint_bridged(user: address, amount: uint256, tx_hash: bytes32):
    assert(msg_sender == self.admin, "Not authorized")
    assert(not self.processed_txs[tx_hash], "Already processed")
    self.processed_txs[tx_hash] = True
    # Logic to mint tokens to user
    pass
