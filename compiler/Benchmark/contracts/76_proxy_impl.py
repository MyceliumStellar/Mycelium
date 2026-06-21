"""Proxy Implementation: logic for an upgradeable proxy."""
value: uint256

@external
def set_value(v: uint256):
    self.value = v

@external
@view
def get_value() -> uint256:
    return self.value
