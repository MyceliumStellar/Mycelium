"""Registry: simple name service for mapping names to addresses."""
owner: address
names: Mapping[bytes32, address]
owners: Mapping[bytes32, address]

@event
class Registered:
    name: indexed(bytes32)
    addr: indexed(address)

@external
def __init__():
    self.owner = msg_sender

@external
def register(name: bytes32, addr: address):
    assert(self.owners[name] == ZERO_ADDRESS, "Already registered")
    self.names[name] = addr
    self.owners[name] = msg_sender
    self.Registered(name, addr)

@external
def update(name: bytes32, addr: address):
    assert(self.owners[name] == msg_sender, "Not owner")
    self.names[name] = addr

@external
@view
def resolve(name: bytes32) -> address:
    return self.names[name]

@external
@view
def get_owner(name: bytes32) -> address:
    return self.owners[name]
