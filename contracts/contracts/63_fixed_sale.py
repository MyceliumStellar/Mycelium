"""Fixed Sale: sell tokens at a fixed price."""
token: address
price: uint256

@external
def __init__(t_addr: address, rate: uint256):
    self.token = t_addr
    self.price = rate

@external
def buy(amount: uint256):
    total_cost: uint256 = amount * self.price
    assert(msg_value >= total_cost, "Insufficient")
    # Transfer tokens
    pass
