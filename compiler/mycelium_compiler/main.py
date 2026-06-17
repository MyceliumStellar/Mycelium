import sys
import os
import argparse
from mycelium_compiler.parser import parse_source
from mycelium_compiler.validator import validate_ast
from mycelium_compiler.codegen import generate_wasm

def compile_file(source_path: str, output_path: str):
    print(f"Compiling {source_path} to {output_path}...")
    with open(source_path, "r") as f:
        source_code = f.read()
    
    visitor = parse_source(source_code)
    validate_ast(visitor)
    wasm_bytes = generate_wasm(visitor)
    
    # Ensure directory exists and write WASM
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        
    with open(output_path, "wb") as f_out:
        f_out.write(wasm_bytes)
    print("Compilation successful!")

def main():
    parser = argparse.ArgumentParser(description="Mycelium Compiler CLI")
    parser.add_argument("source", help="Path to Python source file")
    parser.add_argument("-o", "--output", default="build/target.wasm", help="Output path for WASM")
    args = parser.parse_args()
    
    compile_file(args.source, args.output)

if __name__ == "__main__":
    main()

