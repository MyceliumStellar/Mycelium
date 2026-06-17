"""Oracle Aggregator: aggregate data from multiple oracles."""
oracles: DynArray[address, 5]

@external
def __init__(oracle_list: DynArray[address, 5]):
    self.oracles = oracle_list

@external
@view
def get_average_price() -> uint256:
    # Logic to fetch and average prices
    return 100
