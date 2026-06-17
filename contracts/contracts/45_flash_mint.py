"""Flash Mint: mint and burn tokens in one transaction."""
total_supply: uint256
balances: Mapping[address, uint256]

@external
def flash_mint(amount: uint256):
    self.balances[msg_sender] += amount
    self.total_supply += amount
    # Call receiver logic here...
    self.balances[msg_sender] -= amount
    self.total_supply -= amount
