import unittest
from mycelium_compiler.parser import parse_source
from mycelium_compiler.validator import validate_ast
from mycelium_compiler.codegen import generate_rust_intermediate

class TestMyceliumCompiler(unittest.TestCase):
    def test_basic_contract_parsing(self):
        source = """
from mycelium import contract, state, Symbol, i128

@contract
class MarketOracleAgent:
    provider: Symbol
    price_feed: i128

    @state.instance
    def initialize(self, owner: Symbol, initial_price: i128):
        self.provider = owner
        self.price_feed = initial_price
"""
        visitor = parse_source(source)
        self.assertEqual(visitor.contract_name, "MarketOracleAgent")
        self.assertIn("provider", visitor.state_variables)
        self.assertTrue(validate_ast(visitor))
        
        rust_code = generate_rust_intermediate(visitor)
        self.assertIn("pub struct MarketOracleAgent;", rust_code)

    def test_subscript_mapping_and_symbols(self):
        source = """
from mycelium import contract, state, Symbol, Map, Bytes

@contract
class MyceliumHiveRegistry:
    registry: Map[Bytes, Map[Symbol, Bytes]]

    @state.persistent
    def register_agent(self, agent_id: Bytes, capability_hash: Bytes):
        manifest = Map()
        manifest[Symbol("capability")] = capability_hash
        self.registry[agent_id] = manifest
"""
        visitor = parse_source(source)
        self.assertTrue(validate_ast(visitor))
        rust_code = generate_rust_intermediate(visitor)
        
        # Verify that Map() is transpiled to soroban_sdk::Map::new(&env)
        self.assertIn("soroban_sdk::Map::new(&env)", rust_code)
        # Verify that Symbol is transpiled to Symbol::new(&env, "capability") (with double quotes)
        self.assertIn('Symbol::new(&env, "capability")', rust_code)
        # Verify that local map assignment is transpiled using .set(...)
        self.assertIn('manifest.set(Symbol::new(&env, "capability"), capability_hash.clone());', rust_code)
        # Verify that single quotes character literal warning/error isn't present
        self.assertNotIn("'capability'", rust_code)

    def test_hive_registry_compiles(self):
        source = """
from mycelium import contract, state, Symbol, Map, Bytes, Vec

@contract
class MyceliumHiveRegistry:
    registry: Map[Bytes, Map[Symbol, Bytes]]

    @state.persistent
    def register_agent(self, agent_id: Bytes, capability_hash: Bytes, operational_uri: Bytes):
        manifest = Map()
        manifest[Symbol("capability")] = capability_hash
        manifest[Symbol("endpoint")] = operational_uri
        manifest[Symbol("reputation")] = Bytes(b"100")
        self.registry[agent_id] = manifest

    @state.instance
    def discover_capability(self, target_capability: Bytes) -> Vec[Bytes]:
        matched_agents = Vec()
        for agent_id in self.registry.keys():
            manifest = self.registry[agent_id]
            if manifest[Symbol("capability")] == target_capability:
                matched_agents.append(agent_id)
        return matched_agents
"""
        visitor = parse_source(source)
        self.assertTrue(validate_ast(visitor))
        rust_code = generate_rust_intermediate(visitor)
        
        # Verify Vec[Bytes] mapping
        self.assertIn("-> soroban_sdk::Vec<soroban_sdk::Bytes>", rust_code)
        # Verify For loop translation
        self.assertIn("for agent_id in", rust_code)
        # Verify bytes literal
        self.assertIn('b"100"', rust_code)
        # Verify Bytes conversion does not publish an event
        self.assertNotIn("publish", rust_code)
        self.assertIn("soroban_sdk::Bytes::from_slice", rust_code)
        # Verify append mapping
        self.assertIn("matched_agents.push_back(agent_id);", rust_code)

if __name__ == "__main__":
    unittest.main()
