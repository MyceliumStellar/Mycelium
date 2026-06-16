import sys
import argparse
from mycelium_compiler.parser import MyceliumCompilerVisitor
from mycelium_compiler.validator import validate_ast
from mycelium_compiler.codegen import generate_wasm

def compile_file(source_path: str, output_path: str):
    print(f"Compiling {source_path} to {output_path}...")
    # TODO: Read file, parse AST, validate types, generate WASM
    pass

def main():
    parser = argparse.ArgumentParser(description="Mycelium Compiler CLI")
    parser.add_argument("source", help="Path to Python source file")
    parser.add_argument("-o", "--output", default="build/target.wasm", help="Output path for WASM")
    args = parser.parse_args()
    
    compile_file(args.source, args.output)

if __name__ == "__main__":
    main()
