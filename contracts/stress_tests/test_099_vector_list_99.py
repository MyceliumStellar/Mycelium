from mycelium import contract, state, Symbol, Vec, i128

@contract
class VectorList_99:
    items: Vec[Symbol]

    @state.instance
    def initialize(self):
        pass

    @state.instance
    def add_item(self, item: Symbol):
        self.items.append(item)

    @state.instance
    def length(self) -> i128:
        # returns simple length mock
        return 5
