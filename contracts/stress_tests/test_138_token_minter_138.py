from mycelium import contract, state, Symbol, i128, Map

@contract
class TokenMinter_138:
    balances: Map[Symbol, i128]
    admin: Symbol
    supply: i128

    @state.instance
    def initialize(self, owner: Symbol):
        self.admin = owner
        self.supply = 0

    @state.instance
    def mint(self, target: Symbol, amount: i128) -> bool:
        current = 0
        if target in self.balances:
            current = self.balances[target]
        self.balances[target] = current + amount
        self.supply = self.supply + amount
        return True
