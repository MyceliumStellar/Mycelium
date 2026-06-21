"""Logic Contract: standard logic implementation."""
data: uint256

@external
def initialize(v: uint256):
    self.data = v

@external
def update_data(v: uint256):
    self.data = v

@external
@view
def get_data() -> uint256:
    return self.data
