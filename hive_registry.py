"""
HiveRegistry — the global on-chain directory for Mycelium agents.

Maps a unique alphanumeric agent name to its on-chain identity:
  - address     : the agent's Stellar wallet (G-address)
  - capability   : SHA-256 hash of the agent's sorted capability tags (Bytes)
  - endpoint     : the agent's service endpoint URL, UTF-8 bytes (Bytes)
  - reputation   : an integer reputation score (U64), starts at 0

This contract is authored in the Mycelium DSL and compiled with our own
compiler (`python -m mycelium_compiler.main hive_registry.py -o
build/hive_registry.wasm`). Deploy it once per network and paste the resulting
contract id into `mycelium_sdk/constants.py` (HIVEMIND_REGISTRY_ADDRESS).

The SDK's HiveClient calls `register_agent` / `resolve_agent` here.
"""

from mycelium import (
    contract, external, view,
    Address, U64, Bytes, Bool, Map, Env, Symbol,
)


class ContractError:
    NAME_TAKEN = 1
    NOT_REGISTERED = 2


@contract
class HiveRegistry:
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def register_agent(
        self,
        name: Symbol,
        agent_address: Address,
        capability_hash: Bytes,
        endpoint: Bytes,
        model: Bytes,
        role: Bytes,
        desc: Bytes,
    ) -> Bool:
        """
        Register `name` to the caller's identity. Reverts with NAME_TAKEN if the
        name is already claimed. The caller must control `agent_address`.
        """
        agent_address.require_auth()

        reg_key = "reg:" + str(name)
        if self.storage.get(reg_key, False):
            raise ContractError.NAME_TAKEN

        self.storage.set("addr:" + str(name), agent_address)
        self.storage.set("cap:" + str(name), capability_hash)
        self.storage.set("endp:" + str(name), endpoint)
        self.storage.set("model:" + str(name), model)
        self.storage.set("role:" + str(name), role)
        self.storage.set("desc:" + str(name), desc)
        self.storage.set("rep:" + str(name), U64(0))
        self.storage.set(reg_key, True)

        self.env.emit_event("agent_registered", {"name": name, "address": agent_address})
        return True

    @view
    def resolve_agent(self, name: Symbol) -> Map:
        """Resolve `name` to its directory entry. Reverts if not registered."""
        reg_key = "reg:" + str(name)
        if not self.storage.get(reg_key, False):
            raise ContractError.NOT_REGISTERED

        details = Map()
        details.set(Symbol("address"), self.storage.get("addr:" + str(name)))
        details.set(Symbol("capability"), self.storage.get("cap:" + str(name)))
        details.set(Symbol("endpoint"), self.storage.get("endp:" + str(name)))
        details.set(Symbol("model"), self.storage.get("model:" + str(name)))
        details.set(Symbol("role"), self.storage.get("role:" + str(name)))
        details.set(Symbol("desc"), self.storage.get("desc:" + str(name)))
        details.set(Symbol("reputation"), self.storage.get("rep:" + str(name)))
        return details

    @external
    def update_reputation(self, name: Symbol, new_reputation: U64) -> Bool:
        """Update an agent's reputation score. Reverts if the name is unregistered."""
        reg_key = "reg:" + str(name)
        if not self.storage.get(reg_key, False):
            raise ContractError.NOT_REGISTERED
        self.storage.set("rep:" + str(name), new_reputation)
        return True

    @view
    def is_registered(self, name: Symbol) -> Bool:
        """Return whether `name` is currently registered."""
        return self.storage.get("reg:" + str(name), False)
