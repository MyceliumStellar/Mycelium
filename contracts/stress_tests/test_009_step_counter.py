from mycelium import contract, state, i128
@contract
class StepCounter:
    count: i128
    step: i128
    @state.instance
    def initialize(self, init_step: i128):
        self.count = 0
        self.step = init_step
    @state.instance
    def step_forward(self) -> i128:
        self.count = self.count + self.step
        return self.count
