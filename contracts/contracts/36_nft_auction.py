"""NFT Auction: bid for specific NFT token IDs."""
nft: address
seller: address
token_id: uint256
highest_bid: uint256
highest_bidder: address
end_time: uint256

@external
def __init__(nft_addr: address, id: uint256, duration: uint256):
    self.nft = nft_addr
    self.token_id = id
    self.seller = msg_sender
    self.end_time = block_timestamp + duration

@external
def bid():
    assert(block_timestamp < self.end_time, "Ended")
    assert(msg_value > self.highest_bid, "Bid too low")
    self.highest_bid = msg_value
    self.highest_bidder = msg_sender

@external
def finalize():
    assert(block_timestamp >= self.end_time, "Ongoing")
    # Transfer NFT to winner, ETH to seller
    pass
