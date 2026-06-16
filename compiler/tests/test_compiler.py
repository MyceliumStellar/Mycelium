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

if __name__ == "__main__":
    unittest.main()
