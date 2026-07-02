import os
import sys
import shutil

# Add project root and compiler subdirectory to PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from mycelium_compiler.main import compile_file

def main():
    contracts_dir = "contracts/contracts"
    if not os.path.exists(contracts_dir):
        print(f"Error: Directory {contracts_dir} not found.")
        sys.exit(1)
        
    files = sorted([f for f in os.listdir(contracts_dir) if f.endswith(".py")])
    print(f"Found {len(files)} smart contracts in {contracts_dir}.")
    
    import tempfile
    out_dir = os.path.join(tempfile.gettempdir(), "mycelium_builds")
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    
    passed = []
    failed = []
    
    for filename in files:
        file_path = os.path.join(contracts_dir, filename)
        output_path = os.path.join(out_dir, filename.replace(".py", ".wasm"))
        print(f"\n[{len(passed) + len(failed) + 1}/{len(files)}] Compiling {filename}...")
        try:
            compile_file(file_path, output_path)
            passed.append(filename)
        except Exception as e:
            failed.append((filename, str(e)))
            print(f"FAILED {filename}: {e}")
            
    print("\n================== COMPILATION RESULTS ==================")
    print(f"Total Files:  {len(files)}")
    print(f"Passed:       {len(passed)}")
    print(f"Failed:       {len(failed)}")
    print("=========================================================")
    
    if failed:
        print("\nFailed Contracts:")
        for fname, error in failed:
            print(f"- {fname}")
        sys.exit(1)
    else:
        print("\nAll contracts compiled successfully to WASM!")
        sys.exit(0)

if __name__ == "__main__":
    main()
