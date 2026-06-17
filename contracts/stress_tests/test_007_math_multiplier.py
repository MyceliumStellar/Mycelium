from mycelium import contract, state, i128
@contract
class MathMultiplier:
    factor: i128
    @state.instance
    def initialize(self, val: i128):
        self.factor = val
    @state.instance
    def multiply(self, input_val: i128) -> i128:
        return input_val * self.factor
