from mycelium import contract, state, i128

@contract
class MarketMaker_146:
    token_a: i128
    token_b: i128

    @state.instance
    def initialize(self, a: i128, b: i128):
        self.token_a = a
        self.token_b = b

    @state.instance
    def get_price_ratio(self) -> i128:
        return self.token_a / self.token_b
