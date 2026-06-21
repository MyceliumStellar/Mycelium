"""Prediction Market: bet on binary outcomes."""
outcome_odds: Mapping[bool, uint256]
bets: Mapping[address, Mapping[bool, uint256]]
total_bets: Mapping[bool, uint256]
resolved: bool
winner: bool

@external
def __init__():
    self.resolved = False

@external
def place_bet(side: bool):
    assert(not self.resolved, "Resolved")
    self.bets[msg_sender][side] += msg_value
    self.total_bets[side] += msg_value

@external
def resolve(side: bool):
    # Admin only, resolve and enable payouts
    self.resolved = True
    self.winner = side
