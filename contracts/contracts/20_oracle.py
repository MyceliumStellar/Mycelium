"""Oracle: simple data feed for off-chain information."""
owner: address
data_feeds: Mapping[bytes32, uint256]
feed_updates: Mapping[bytes32, uint256]

@event
class FeedUpdated:
    id: indexed(bytes32)
    value: uint256
    timestamp: uint256

@external
def __init__():
    self.owner = msg_sender

@external
def update_feed(feed_id: bytes32, value: uint256):
    assert(msg_sender == self.owner, "Not owner")
    self.data_feeds[feed_id] = value
    self.feed_updates[feed_id] = block_timestamp
    self.FeedUpdated(feed_id, value, block_timestamp)

@external
@view
def get_feed(feed_id: bytes32) -> uint256:
    return self.data_feeds[feed_id]

@external
@view
def get_last_update(feed_id: bytes32) -> uint256:
    return self.feed_updates[feed_id]
