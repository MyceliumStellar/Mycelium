import os
import sys

# Add project root and compiler subdirectory to PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from mycelium_compiler.parser import parse_source
from mycelium_compiler.validator import validate_ast
from mycelium_compiler.codegen import generate_rust_intermediate

def run_stress_tests():
    dir_path = "contracts/stress_tests"
    if not os.path.exists(dir_path):
        print(f"Error: Directory {dir_path} not found.")
        sys.exit(1)
        
    files = sorted([f for f in os.listdir(dir_path) if f.endswith(".py")])
    print(f"Found {len(files)} smart contracts in {dir_path}.")
    
    passed = []
    failed = []
    
    for filename in files:
        file_path = os.path.join(dir_path, filename)
        with open(file_path, "r", encoding="utf-8") as f:
            source_code = f.read()
            
        try:
            visitor = parse_source(source_code)
            validate_ast(visitor)
            rust_code = generate_rust_intermediate(visitor)
            passed.append(filename)
        except Exception as e:
            failed.append((filename, str(e)))
            
    print("\n================== STRESS TEST RESULTS ==================")
    print(f"Total Files:  {len(files)}")
    print(f"Passed:       {len(passed)}")
    print(f"Failed:       {len(failed)}")
    print("=========================================================")
    
    if failed:
        print("\nFailed Compilation Details:")
        for fname, error in failed:
            print(f"- {fname}: {error}")
        sys.exit(1)
    else:
        print("\nAll 150 smart contracts successfully compiled!")
        sys.exit(0)

if __name__ == "__main__":
    run_stress_tests()
