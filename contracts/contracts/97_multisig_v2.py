"""Multi-sig V2: advanced multisig with transaction queuing."""
owners: Mapping[address, bool]
required: uint256
tx_count: uint256
tx_queue: Mapping[uint256, address]
tx_approvals: Mapping[uint256, uint256]

@external
def __init__(req: uint256):
    self.owners[msg_sender] = True
    self.required = req
    self.tx_count = 0

@external
def queue_transaction(target: address):
    tx_id: uint256 = self.tx_count
    self.tx_queue[tx_id] = target
    self.tx_count += 1

@external
def approve(tx_id: uint256):
    assert(self.owners[msg_sender], "Not owner")
    self.tx_approvals[tx_id] += 1
