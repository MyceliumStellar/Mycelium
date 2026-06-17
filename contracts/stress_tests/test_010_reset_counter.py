from mycelium import contract, state, i128
@contract
class ResetCounter:
    count: i128
    @state.instance
    def initialize(self):
        self.count = 0
    @state.instance
    def reset(self):
        self.count = 0
