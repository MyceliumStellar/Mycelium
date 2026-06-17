"""NFT Registry: mint unique tokens with metadata."""
owner: address
token_count: uint256
token_owner: Mapping[uint256, address]
token_uri: Mapping[uint256, String]
owned_count: Mapping[address, uint256]
approved: Mapping[uint256, address]
approved_all: Mapping[address, Mapping[address, bool]]

@event
class Transfer:
    from_addr: indexed(address)
    to_addr: indexed(address)
    token_id: indexed(uint256)

@event
class Approval:
    owner: indexed(address)
    approved: indexed(address)
    token_id: indexed(uint256)

@external
def __init__():
    self.owner = msg_sender
    self.token_count = 0

@external
def mint(to: address, uri: String) -> uint256:
    assert(msg_sender == self.owner, "Not owner")
    token_id: uint256 = self.token_count
    self.token_owner[token_id] = to
    self.token_uri[token_id] = uri
    self.owned_count[to] += 1
    self.token_count += 1
    self.Transfer(ZERO_ADDRESS, to, token_id)
    return token_id

@external
def transfer(to: address, token_id: uint256):
    assert(self.token_owner[token_id] == msg_sender, "Not owner")
    self.owned_count[msg_sender] -= 1
    self.owned_count[to] += 1
    self.token_owner[token_id] = to
    self.Transfer(msg_sender, to, token_id)

@external
def approve(to: address, token_id: uint256):
    assert(self.token_owner[token_id] == msg_sender, "Not owner")
    self.approved[token_id] = to
    self.Approval(msg_sender, to, token_id)

@external
@view
def owner_of(token_id: uint256) -> address:
    return self.token_owner[token_id]

@external
@view
def token_uri_of(token_id: uint256) -> String:
    return self.token_uri[token_id]

@external
@view
def balance_of(account: address) -> uint256:
    return self.owned_count[account]
