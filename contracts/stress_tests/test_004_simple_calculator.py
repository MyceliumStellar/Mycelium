from mycelium import contract, state, i128
@contract
class SimpleCalculator:
    @state.instance
    def add(self, a: i128, b: i128) -> i128:
        return a + b
    @state.instance
    def sub(self, a: i128, b: i128) -> i128:
        return a - b
