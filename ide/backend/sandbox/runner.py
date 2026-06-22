from ide.backend.sandbox.docker_runner import compile_in_docker_sandbox
import os
import shutil
import io
import sys

def compile_in_host_sandbox(source_code: str) -> dict:
    """
    Executes compilation inside the containerized Docker sandbox.
    If USE_LOCAL_COMPILER is true or if docker is not available,
    it falls back to running the compiler in-process.
    """
    use_local = os.getenv("USE_LOCAL_COMPILER", "false").lower() == "true"
    
    # Check if docker is available in system PATH
    docker_available = bool(shutil.which("docker"))
    
    if use_local or not docker_available:
        try:
            from mycelium_compiler.parser import parse_source
            from mycelium_compiler.validator import validate_ast
            from mycelium_compiler.codegen import generate_wasm
            
            # Capture stdout and stderr to match docker runner outputs
            old_stdout = sys.stdout
            old_stderr = sys.stderr
            sys.stdout = stdout_capture = io.StringIO()
            sys.stderr = stderr_capture = io.StringIO()
            
            try:
                visitor = parse_source(source_code)
                validate_ast(visitor)
                wasm_bytes = generate_wasm(visitor)
                success = True
            except Exception as compile_exc:
                wasm_bytes = b""
                success = False
                print(f"Compilation exception: {compile_exc}", file=sys.stderr)
            
            stdout_val = stdout_capture.getvalue()
            stderr_val = stderr_capture.getvalue()
            
            # Restore stdout/stderr
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            
            return {
                "success": success,
                "wasm_bytes": wasm_bytes,
                "stdout": stdout_val,
                "stderr": stderr_val
            }
        except Exception as e:
            return {
                "success": False,
                "wasm_bytes": b"",
                "stdout": "",
                "stderr": f"Error running in-process compiler fallback: {str(e)}"
            }
            
    return compile_in_docker_sandbox(source_code)
