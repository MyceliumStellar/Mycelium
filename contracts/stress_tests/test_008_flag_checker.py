from mycelium import contract, state
@contract
class FlagChecker:
    flag_a: bool
    flag_b: bool
    @state.instance
    def initialize(self):
        self.flag_a = True
        self.flag_b = False
    @state.instance
    def check_both(self) -> bool:
        return self.flag_a and self.flag_b
