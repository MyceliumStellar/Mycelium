"""Marketplace: buy and sell items with ETH."""
owner: address
item_count: uint256
item_price: Mapping[uint256, uint256]
item_seller: Mapping[uint256, address]
item_sold: Mapping[uint256, bool]

@event
class ItemListed:
    id: indexed(uint256)
    seller: indexed(address)
    price: uint256

@event
class ItemBought:
    id: indexed(uint256)
    buyer: indexed(address)
    price: uint256

@external
def __init__():
    self.owner = msg_sender
    self.item_count = 0

@external
def list_item(price: uint256) -> uint256:
    assert(price > 0, "Zero price")
    item_id: uint256 = self.item_count
    self.item_price[item_id] = price
    self.item_seller[item_id] = msg_sender
    self.item_sold[item_id] = False
    self.item_count += 1
    self.ItemListed(item_id, msg_sender, price)
    return item_id

@external
def buy_item(item_id: uint256):
    assert(item_id < self.item_count, "Invalid ID")
    assert(not self.item_sold[item_id], "Already sold")
    assert(msg_value >= self.item_price[item_id], "Insufficient payment")
    
    seller: address = self.item_seller[item_id]
    self.item_sold[item_id] = True
    self.ItemBought(item_id, msg_sender, self.item_price[item_id])

@external
@view
def get_item(item_id: uint256) -> (uint256, address, bool):
    return (self.item_price[item_id], self.item_seller[item_id], self.item_sold[item_id])
