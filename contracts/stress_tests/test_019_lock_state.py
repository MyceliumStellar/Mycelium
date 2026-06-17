from mycelium import contract, state
@contract
class LockState:
    locked: bool
    @state.instance
    def initialize(self):
        self.locked = False
    @state.instance
    def lock(self):
        self.locked = True
