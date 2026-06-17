"""Crowdsale: crowdsale with bonus tiers."""
goal: uint256
raised: uint256

@external
def contribute():
    bonus: uint256 = 0
    if self.raised < 1000:
        bonus = 20
    elif self.raised < 5000:
        bonus = 10
    # Logic to credit tokens with bonus
    self.raised += msg_value
