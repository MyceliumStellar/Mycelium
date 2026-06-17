from mycelium import contract, state, i128
@contract
class TimestampMock:
    last_updated: i128
    @state.instance
    def initialize(self, time: i128):
        self.last_updated = time
