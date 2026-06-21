"""Auction: sealed-bid English auction with reserve price."""
owner: address
beneficiary: address
highest_bidder: address
highest_bid: uint256
auction_end: uint256
ended: bool
bids: Mapping[address, uint256]

@event
class BidPlaced:
    bidder: indexed(address)
    amount: uint256

@event
class AuctionEnded:
    winner: indexed(address)
    amount: uint256

@external
def __init__(duration: uint256, beneficiary: address):
    self.owner = msg_sender
    self.beneficiary = beneficiary
    self.auction_end = block_timestamp + duration
    self.ended = False
    self.highest_bid = 0

@external
def bid():
    assert(block_timestamp < self.auction_end, "Auction over")
    assert(msg_value > self.highest_bid, "Bid too low")
    if self.highest_bidder != ZERO_ADDRESS:
        self.bids[self.highest_bidder] += self.highest_bid
    self.highest_bidder = msg_sender
    self.highest_bid = msg_value
    self.BidPlaced(msg_sender, msg_value)

@external
def withdraw():
    amount: uint256 = self.bids[msg_sender]
    assert(amount > 0, "Nothing to withdraw")
    self.bids[msg_sender] = 0

@external
def end_auction():
    assert(block_timestamp >= self.auction_end, "Not ended")
    assert(not self.ended, "Already ended")
    self.ended = True
    self.AuctionEnded(self.highest_bidder, self.highest_bid)

@external
@view
def get_highest_bid() -> uint256:
    return self.highest_bid

@external
@view
def get_highest_bidder() -> address:
    return self.highest_bidder
