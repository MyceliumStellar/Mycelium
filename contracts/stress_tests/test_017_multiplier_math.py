from mycelium import contract, state, i128
@contract
class MultiplierMath:
    @state.instance
    def square(self, val: i128) -> i128:
        return val * val
