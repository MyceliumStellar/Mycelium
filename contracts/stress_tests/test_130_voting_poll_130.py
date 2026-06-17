from mycelium import contract, state, Symbol, i128, Map

@contract
class VotingPoll_130:
    votes: Map[Symbol, i128]
    admin: Symbol

    @state.instance
    def initialize(self, creator: Symbol):
        self.admin = creator

    @state.instance
    def vote(self, candidate: Symbol) -> i128:
        current = 0
        if candidate in self.votes:
            current = self.votes[candidate]
        self.votes[candidate] = current + 1
        return current + 1
