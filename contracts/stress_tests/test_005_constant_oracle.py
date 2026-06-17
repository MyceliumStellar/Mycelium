from mycelium import contract, state, i128
@contract
class ConstantOracle:
    price: i128
    @state.instance
    def initialize(self):
        self.price = 100
    @state.instance
    def get_price(self) -> i128:
        return self.price
