from mycelium import contract, state, Symbol, i128

@contract
class BasicAgent:
    owner: Symbol
    counter: i128

    @state.instance
    def initialize(self, owner: Symbol):
        self.owner = owner
        self.counter = 0

    @state.instance
    def increment(self) -> i128:
        self.counter = self.counter + 1
        return self.counter
