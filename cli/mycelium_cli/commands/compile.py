import os
import sys
from mycelium_compiler.parser import parse_source
from mycelium_compiler.validator import validate_ast
from mycelium_compiler.codegen import generate_wasm, generate_rust_intermediate

def run_compile(file_path: str, output_path: str):
    print(f"Compiling contract {file_path}...")
    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found.")
        sys.exit(1)
        
    with open(file_path, "r") as f:
        source_code = f.read()
        
    try:
        visitor = parse_source(source_code)
        validate_ast(visitor)
        
        # Generates target WASM bytecode
        wasm_bytes = generate_wasm(visitor)
        
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f_out:
            f_out.write(wasm_bytes)
            
        print(f"✓ Compilation successful! Output generated: {output_path}")
    except Exception as e:
        print(f"❌ Compilation failed: {e}")
        sys.exit(1)
