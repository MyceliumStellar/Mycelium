from mycelium import contract, state, Symbol
@contract
class PermissionFlag:
    allowed: bool
    @state.instance
    def initialize(self):
        self.allowed = True
    @state.instance
    def revoke(self):
        self.allowed = False
