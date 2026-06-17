"""Auto-compounder: automatically reinvest rewards."""
farm: address

@external
def __init__(farm_addr: address):
    self.farm = farm_addr

@external
def compound():
    # Harvest rewards and reinvest into LP
    pass
