"""Access Control: roles and permissions."""
owner: address
roles: Mapping[address, Mapping[uint256, bool]]

ADMIN_ROLE: uint256
MINTER_ROLE: uint256
PAUSER_ROLE: uint256

@event
class RoleGranted:
    account: indexed(address)
    role: uint256

@event
class RoleRevoked:
    account: indexed(address)
    role: uint256

@external
def __init__():
    self.owner = msg_sender
    self.ADMIN_ROLE = 0
    self.MINTER_ROLE = 1
    self.PAUSER_ROLE = 2
    self.roles[msg_sender][0] = True

@external
def grant_role(account: address, role: uint256):
    assert(self.roles[msg_sender][0], "Not admin")
    self.roles[account][role] = True
    self.RoleGranted(account, role)

@external
def revoke_role(account: address, role: uint256):
    assert(self.roles[msg_sender][0], "Not admin")
    self.roles[account][role] = False
    self.RoleRevoked(account, role)

@external
@view
def has_role(account: address, role: uint256) -> bool:
    return self.roles[account][role]
