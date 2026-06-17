from mycelium import contract, state, Symbol
@contract
class GreetingContract:
    greeting: Symbol
    @state.instance
    def initialize(self):
        self.greeting = Symbol("HELLO")
    @state.instance
    def greet(self) -> Symbol:
        return self.greeting
