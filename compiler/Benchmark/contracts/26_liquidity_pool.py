"""Liquidity Pool: swap tokens based on constant product formula."""
token0: address
token1: address
reserve0: uint256
reserve1: uint256
total_supply: uint256
balances: Mapping[address, uint256]

@event
class Mint:
    sender: indexed(address)
    amount0: uint256
    amount1: uint256

@event
class Swap:
    sender: indexed(address)
    amount0_in: uint256
    amount1_in: uint256
    amount0_out: uint256
    amount1_out: uint256

@external
def __init__(t0: address, t1: address):
    self.token0 = t0
    self.token1 = t1

@external
def add_liquidity(amount0: uint256, amount1: uint256) -> uint256:
    # Simplified mint logic
    liquidity: uint256 = (amount0 * amount1) # Very basic
    self.balances[msg_sender] += liquidity
    self.total_supply += liquidity
    self.reserve0 += amount0
    self.reserve1 += amount1
    self.Mint(msg_sender, amount0, amount1)
    return liquidity

@external
def swap(amount0_out: uint256, amount1_out: uint256):
    assert(amount0_out > 0 or amount1_out > 0, "Zero output")
    # Basic swap check logic would go here
    self.Swap(msg_sender, 0, 0, amount0_out, amount1_out)

@external
@view
def get_reserves() -> (uint256, uint256):
    return (self.reserve0, self.reserve1)
