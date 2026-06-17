from mycelium import contract, state, Symbol, i128
@contract
class BasicCounter:
    val: i128
    @state.instance
    def initialize(self):
        self.val = 0
    @state.instance
    def increment(self) -> i128:
        self.val = self.val + 1
        return self.val
