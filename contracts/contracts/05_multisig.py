"""Multisig: N-of-M transaction approvals."""
owners: Mapping[address, bool]
required: uint256
owner_count: uint256
tx_count: uint256
tx_to: Mapping[uint256, address]
tx_value: Mapping[uint256, uint256]
tx_executed: Mapping[uint256, bool]
tx_approvals: Mapping[uint256, uint256]
approved: Mapping[uint256, Mapping[address, bool]]

@event
class Submitted:
    tx_id: indexed(uint256)
    to: address
    value: uint256

@event
class Approved:
    tx_id: indexed(uint256)
    approver: indexed(address)

@event
class Executed:
    tx_id: indexed(uint256)

@external
def __init__(required: uint256):
    assert(required > 0, "Need at least 1")
    self.required = required
    self.owners[msg_sender] = True
    self.owner_count = 1

@external
def add_owner(owner: address):
    assert(self.owners[msg_sender], "Not owner")
    assert(not self.owners[owner], "Already owner")
    self.owners[owner] = True
    self.owner_count += 1

@external
def submit(to: address, value: uint256) -> uint256:
    assert(self.owners[msg_sender], "Not owner")
    tx_id: uint256 = self.tx_count
    self.tx_to[tx_id] = to
    self.tx_value[tx_id] = value
    self.tx_executed[tx_id] = False
    self.tx_approvals[tx_id] = 0
    self.tx_count += 1
    self.Submitted(tx_id, to, value)
    return tx_id

@external
def approve(tx_id: uint256):
    assert(self.owners[msg_sender], "Not owner")
    assert(not self.approved[tx_id][msg_sender], "Already approved")
    self.approved[tx_id][msg_sender] = True
    self.tx_approvals[tx_id] += 1
    self.Approved(tx_id, msg_sender)

@external
def execute(tx_id: uint256):
    assert(not self.tx_executed[tx_id], "Already executed")
    assert(self.tx_approvals[tx_id] >= self.required, "Not enough approvals")
    self.tx_executed[tx_id] = True
    self.Executed(tx_id)

@external
@view
def is_approved(tx_id: uint256, owner: address) -> bool:
    return self.approved[tx_id][owner]
