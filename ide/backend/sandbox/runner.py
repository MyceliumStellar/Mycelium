import subprocess
import tempfile
import os
import sys

def compile_in_host_sandbox(source_code: str) -> dict:
    """
    Executes mycelium compilation in an isolated workspace path on the host.
    """
    # Create a temporary directory to act as the build sandbox
    with tempfile.TemporaryDirectory() as tmpdir:
        source_file_path = os.path.join(tmpdir, "contract.py")
        output_wasm_path = os.path.join(tmpdir, "target.wasm")
        
        with open(source_file_path, "w") as f:
            f.write(source_code)
            
        # Execute mycelium CLI compile command using subprocess
        # Python interpreter running compiler main
        cmd = [
            sys.executable,
            "-m", "mycelium_compiler.main",
            source_file_path,
            "-o", output_wasm_path
        ]
        
        # Setup environment containing compiler imports
        env = os.environ.copy()
        # Ensure compiler is in pythonpath
        env["PYTHONPATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "compiler"))
        
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
        
        success = res.returncode == 0
        wasm_bytes = b""
        if success and os.path.exists(output_wasm_path):
            with open(output_wasm_path, "rb") as f_wasm:
                wasm_bytes = f_wasm.read()
                
        return {
            "success": success,
            "wasm_bytes": wasm_bytes,
            "stdout": res.stdout,
            "stderr": res.stderr
        }
