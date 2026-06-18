import subprocess
import tempfile
import os
import sys

def compile_in_docker_sandbox(source_code: str) -> dict:
    """
    Runs compiler inside an isolated Docker container for security and consistency.
    """
    # Create a temporary directory on the host to share files via volume mount
    with tempfile.TemporaryDirectory() as tmpdir:
        source_file_path = os.path.join(tmpdir, "contract.py")
        output_wasm_path = os.path.join(tmpdir, "target.wasm")
        
        # Write user python contract code
        with open(source_file_path, "w", encoding="utf-8") as f:
            f.write(source_code)
            
        # Command to run docker container
        cmd = [
            "docker", "run", "--rm",
            "-v", f"{os.path.abspath(tmpdir)}:/workspace",
            "--network", "none",            # Sandbox: disable network access
            "--memory", "512m",             # Quota: limit RAM
            "--cpus", "1.0",                # Quota: limit CPU allocation
            "mycelium-compiler:latest"
        ]
        
        try:
            # Execute compilation with a 30-second timeout
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
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
            
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "wasm_bytes": b"",
                "stdout": "",
                "stderr": "Error: Compilation request timed out (limit: 30 seconds)."
            }
        except Exception as e:
            return {
                "success": False,
                "wasm_bytes": b"",
                "stdout": "",
                "stderr": f"Error initiating docker container: {str(e)}"
            }
