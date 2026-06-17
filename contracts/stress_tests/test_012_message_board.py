from mycelium import contract, state, Symbol
@contract
class MessageBoard:
    message: Symbol
    @state.instance
    def initialize(self, initial_msg: Symbol):
        self.message = initial_msg
    @state.instance
    def update_msg(self, new_msg: Symbol):
        self.message = new_msg
