"""Token Sale: ICO style token sale contract."""
token: address
rate: uint256

@external
def __init__(t_addr: address, r: uint256):
    self.token = t_addr
    self.rate = r

@external
def buy_tokens():
    amount: uint256 = msg_value * self.rate
    # Transfer tokens to buyer
    pass
