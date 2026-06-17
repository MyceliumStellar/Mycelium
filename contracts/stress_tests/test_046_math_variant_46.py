from mycelium import contract, state, i128

@contract
class MathVariant_46:
    factor: i128

    @state.instance
    def initialize(self, init_factor: i128):
        self.factor = init_factor

    @state.instance
    def compute(self, x: i128) -> i128:
        return (x + 46) * self.factor
