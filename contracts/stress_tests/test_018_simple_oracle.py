from mycelium import contract, state, i128, Symbol
@contract
class SimpleOracle:
    provider: Symbol
    price: i128
    @state.instance
    def initialize(self, owner: Symbol):
        self.provider = owner
        self.price = 0
