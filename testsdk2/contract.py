"""Inner logic: a strictly-typed Mycelium → Soroban contract."""

from mycelium import contract, external, view, Env, U64


@contract
class Counter:
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def increment(self) -> U64:
        count = self.storage.get("count", U64(0))
        count = count + U64(1)
        self.storage.set("count", count)
        return count

    @view
    def get_count(self) -> U64:
        return self.storage.get("count", U64(0))
