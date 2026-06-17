"""Asset Registry: registry of approved tokens and assets."""
owner: address
assets: Mapping[address, bool]

@external
def __init__():
    self.owner = msg_sender

@external
def add_asset(asset: address):
    assert(msg_sender == self.owner, "Not owner")
    self.assets[asset] = True

@external
@view
def is_approved(asset: address) -> bool:
    return self.assets[asset]
