"""Token Vesting: manage multiple vesting schedules."""
token: address
vesting_amounts: Mapping[address, uint256]
vesting_starts: Mapping[address, uint256]
vesting_durations: Mapping[address, uint256]

@external
def __init__(token_addr: address):
    self.token = token_addr

@external
def add_schedule(user: address, amount: uint256, duration: uint256):
    self.vesting_amounts[user] = amount
    self.vesting_starts[user] = block_timestamp
    self.vesting_durations[user] = duration

@external
def claim():
    # Logic to calculate vested amount and transfer
    pass
