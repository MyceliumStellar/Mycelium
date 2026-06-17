from mycelium import contract, state, Symbol
@contract
class StringSymbol:
    name: Symbol
    @state.instance
    def initialize(self, val: Symbol):
        self.name = val
    @state.instance
    def check_name(self, test: Symbol) -> bool:
        return self.name == test
