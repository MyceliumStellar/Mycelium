from mycelium import contract, state, Symbol, i128, Map

@contract
class MapTracker_74:
    store: Map[Symbol, i128]
    total: i128

    @state.instance
    def initialize(self):
        self.total = 0

    @state.instance
    def set_value(self, key: Symbol, val: i128):
        self.store[key] = val
        self.total = self.total + val

    @state.instance
    def get_value(self, key: Symbol) -> i128:
        if key in self.store:
            return self.store[key]
        return 0
