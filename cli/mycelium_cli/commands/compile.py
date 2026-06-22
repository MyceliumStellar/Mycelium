"""
`mycelium compile` — compile the project's contract to Soroban WASM.

Delegates directly to the compiler pipeline (parse → validate → generate_wasm),
which builds via the pinned stellar-cli 27.0.0 toolchain.
"""

import os
import sys

from mycelium_compiler.parser import parse_source
from mycelium_compiler.validator import validate_ast
from mycelium_compiler.codegen import generate_wasm

from mycelium_cli.config import get_value


def run_compile(
    file_path: str | None = None,
    output_path: str | None = None,
    optimize: bool = False,
) -> str:
    """
    Compile `file_path` to `output_path`. When omitted, both are read from
    mycelium.toml ([onchain].source_contract / [onchain].target_wasm). Prints
    size telemetry. Returns the output path.
    """
    file_path = file_path or get_value("onchain", "source_contract", "contract.py")
    output_path = output_path or get_value("onchain", "target_wasm", "build/contract.wasm")

    print(f"Compiling contract {file_path} -> {output_path}...")
    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found.")
        sys.exit(1)

    with open(file_path, "r") as f:
        source_code = f.read()

    try:
        visitor = parse_source(source_code)
        validate_ast(visitor)
        wasm_bytes = generate_wasm(visitor)
    except Exception as e:
        print(f"❌ Compilation failed: {e}")
        sys.exit(1)

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_path, "wb") as f_out:
        f_out.write(wasm_bytes)

    size = len(wasm_bytes)
    print(f"✓ Compilation successful! Output: {output_path}")
    print(f"  WASM size: {size:,} bytes ({size / 1024:.2f} KiB)")
    # The release profile already optimizes for size (opt-level \"z\", LTO).
    if optimize:
        print("  (--optimize: size-optimized release profile is always applied)")
    return output_path
