"""Factory: deploy other contracts dynamically."""
owner: address
deployed_contracts: Mapping[uint256, address]
count: uint256

@event
class ContractDeployed:
    id: uint256
    addr: address

@external
def __init__():
    self.owner = msg_sender
    self.count = 0

@external
def deploy_child() -> address:
    assert(msg_sender == self.owner, "Not owner")
    # Simplified: in real scenario, uses create_forwarder_to or similar
    child_addr: address = msg_sender # Placeholder
    self.deployed_contracts[self.count] = child_addr
    self.ContractDeployed(self.count, child_addr)
    self.count += 1
    return child_addr

@external
@view
def get_contract(id: uint256) -> address:
    return self.deployed_contracts[id]
