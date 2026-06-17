from mycelium import contract, state, i128
@contract
class AgeTracker:
    age: i128
    @state.instance
    def initialize(self, init_age: i128):
        self.age = init_age
    @state.instance
    def has_birthday(self):
        self.age = self.age + 1
