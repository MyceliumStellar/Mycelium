"""Inner logic: an on-chain accumulator the agent must drive to a target.

Exposes increment / add / get_count so an LLM agent has to *reason* about which
operations to combine to reach a goal value, issuing several real Soroban
transactions in sequence.
"""

from mycelium import contract, external, view, Env, U64


@contract
class Accumulator:
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def increment(self) -> U64:
        count = self.storage.get("count", U64(0))
        count = count + U64(1)
        self.storage.set("count", count)
        return count

    @external
    def add(self, amount: U64) -> U64:
        count = self.storage.get("count", U64(0))
        count = count + amount
        self.storage.set("count", count)
        return count

    @view
    def get_count(self) -> U64:
        return self.storage.get("count", U64(0))
