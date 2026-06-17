"""Dutch Auction: price decreases linearly over time."""
start_price: uint256
end_price: uint256
start_time: uint256
end_time: uint256
sold: bool

@external
def __init__(s_price: uint256, e_price: uint256, duration: uint256):
    self.start_price = s_price
    self.end_price = e_price
    self.start_time = block_timestamp
    self.end_time = block_timestamp + duration
    self.sold = False

@external
def buy():
    price: uint256 = self.get_price()
    assert(msg_value >= price, "Insufficient")
    self.sold = True

@external
@view
def get_price() -> uint256:
    elapsed: uint256 = block_timestamp - self.start_time
    # Linear price drop logic
    return self.start_price # Placeholder
