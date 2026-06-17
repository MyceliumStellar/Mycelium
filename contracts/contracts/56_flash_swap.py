"""Flash Swap: arbitrage using flash swaps."""
pool: address

@external
def __init__(pool_addr: address):
    self.pool = pool_addr

@external
def initiate_swap(amount: uint256):
    # Logic to call pool for flash swap
    pass
