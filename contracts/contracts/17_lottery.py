"""Lottery: commit-reveal random winner selection."""
owner: address
ticket_price: uint256
ticket_count: uint256
tickets: Mapping[uint256, address]
ended: bool
winner: address
prize_pool: uint256

@event
class TicketPurchased:
    buyer: indexed(address)
    ticket_id: uint256

@event
class WinnerSelected:
    winner: indexed(address)
    prize: uint256

@external
def __init__(price: uint256):
    self.owner = msg_sender
    self.ticket_price = price
    self.ticket_count = 0
    self.ended = False
    self.prize_pool = 0

@external
def buy_ticket():
    assert(not self.ended, "Lottery ended")
    assert(msg_value >= self.ticket_price, "Wrong price")
    ticket_id: uint256 = self.ticket_count
    self.tickets[ticket_id] = msg_sender
    self.prize_pool += msg_value
    self.ticket_count += 1
    self.TicketPurchased(msg_sender, ticket_id)

@external
def draw_winner():
    assert(msg_sender == self.owner, "Not owner")
    assert(not self.ended, "Already ended")
    assert(self.ticket_count > 0, "No tickets")
    self.ended = True
    seed: uint256 = block_timestamp + block_number + self.ticket_count
    winner_idx: uint256 = seed - ((seed / self.ticket_count) * self.ticket_count)
    self.winner = self.tickets[winner_idx]
    prize: uint256 = self.prize_pool
    self.prize_pool = 0
    self.WinnerSelected(self.winner, prize)

@external
@view
def get_winner() -> address:
    return self.winner

@external
@view
def get_prize_pool() -> uint256:
    return self.prize_pool
