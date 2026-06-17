from mycelium import contract, state, i128
@contract
class VersionControl:
    version: i128
    @state.instance
    def initialize(self):
        self.version = 100
    @state.instance
    def upgrade(self):
        self.version = self.version + 1
