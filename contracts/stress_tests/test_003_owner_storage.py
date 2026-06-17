from mycelium import contract, state, Symbol
@contract
class OwnerStorage:
    owner: Symbol
    @state.instance
    def initialize(self, initial_owner: Symbol):
        self.owner = initial_owner
    @state.instance
    def get_owner(self) -> Symbol:
        return self.owner
