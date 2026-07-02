import os
import sys
from mycelium_compiler.parser import parse_source
from mycelium_compiler.validator import validate_ast

def run_check(file_path: str):
    print(f"Checking AST rules and type constraints for: {file_path}")
    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found.")
        sys.exit(1)
        
    with open(file_path, "r", encoding="utf-8") as f:
        source_code = f.read()
        
    try:
        visitor = parse_source(source_code)
        validate_ast(visitor)
        print("✓ All validation checks passed successfully!")
    except Exception as e:
        print(f"❌ Validation failed: {e}")
        sys.exit(1)
