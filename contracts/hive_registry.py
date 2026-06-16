from mycelium_compiler.types import contract, state, Symbol, Bytes, Vec, Map

@contract
class MyceliumHiveRegistry:
    # Storage maps agent public key identifier to their functional attributes metadata
    # Metadata includes capability hash, service endpoint strings, and reputation score
    registry: Map[Bytes, Map[Symbol, Bytes]]

    @state.persistent
    def register_agent(self, agent_id: Bytes, capability_hash: Bytes, operational_uri: Bytes):
        manifest = Map()
        manifest[Symbol("capability")] = capability_hash
        manifest[Symbol("endpoint")] = operational_uri
        manifest[Symbol("reputation")] = Bytes(b"100") # Base status initialization
        
        self.registry[agent_id] = manifest

    @state.instance
    def discover_capability(self, target_capability: Bytes) -> Vec[Bytes]:
        matched_agents = Vec()
        for agent_id in self.registry.keys():
            manifest = self.registry[agent_id]
            if manifest[Symbol("capability")] == target_capability:
                matched_agents.append(agent_id)
        return matched_agents
