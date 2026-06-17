"""Token Swap: 1:1 token swap."""
token_a: address
token_b: address

@external
def __init__(a: address, b: address):
    self.token_a = a
    self.token_b = b

@external
def swap_a_for_b(amount: uint256):
    # Logic to take A and give B
    pass
