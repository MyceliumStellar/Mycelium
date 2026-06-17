from mycelium import contract, state, Symbol, i128
@contract
class ConfigStore:
    version: i128
    admin: Symbol
    @state.instance
    def initialize(self, creator: Symbol):
        self.version = 1
        self.admin = creator
