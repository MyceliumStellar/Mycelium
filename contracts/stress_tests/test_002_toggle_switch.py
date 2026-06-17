from mycelium import contract, state, Symbol
@contract
class ToggleSwitch:
    active: bool
    @state.instance
    def initialize(self):
        self.active = False
    @state.instance
    def toggle(self) -> bool:
        self.active = not self.active
        return self.active
