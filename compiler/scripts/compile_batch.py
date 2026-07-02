#!/usr/bin/env python3
"""
compile_batch.py — Compile every Mycelium contract in one or more directories to
WASM using the in-image compiler (parser -> validator -> generate_wasm).

Designed to run INSIDE the compiler Docker image, where the pre-warmed static
workspace (/app/mycelium_contract_workspace) and cargo cache (/app/cargo_target)
make `generate_wasm` work fully offline.

Usage:
    python3 compile_batch.py <input_dir> [<input_dir> ...] [--out <dir>]

For each `*.py` it writes `<out>/<relpath>.wasm` and prints OK/FAIL, then a
summary with the pass count and the list of failures. Exit code is non-zero if
any contract failed, so it can gate CI.
"""
import argparse
import os
import sys
import glob
import traceback

from mycelium_compiler.parser import parse_source
from mycelium_compiler.validator import validate_ast
from mycelium_compiler.codegen import generate_wasm


def find_contracts(input_dir):
    return sorted(glob.glob(os.path.join(input_dir, "**", "*.py"), recursive=True))


def compile_one(src_path):
    """Return (wasm_bytes, None) on success or (None, error_str) on failure."""
    try:
        with open(src_path, encoding="utf-8") as f:
            source = f.read()
        visitor = parse_source(source)
        validate_ast(visitor)
        return generate_wasm(visitor), None
    except Exception as e:  # noqa: BLE001 — report any failure, keep going
        # Keep the message short; full traceback goes to stderr for debugging.
        traceback.print_exc()
        return None, f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"[:200]


def main():
    ap = argparse.ArgumentParser(description="Batch-compile Mycelium contracts to WASM")
    ap.add_argument("inputs", nargs="+", help="Input directories containing *.py contracts")
    ap.add_argument("--out", default="/workspace/out", help="Output directory for .wasm files")
    args = ap.parse_args()

    ok = 0
    failures = []
    total = 0

    for input_dir in args.inputs:
        contracts = find_contracts(input_dir)
        print(f"\n=== {input_dir} : {len(contracts)} contracts ===", flush=True)
        for i, src in enumerate(contracts):
            total += 1
            rel = os.path.relpath(src, input_dir)
            label = os.path.join(os.path.basename(input_dir.rstrip("/")), rel)
            print(f"[{i+1}/{len(contracts)}] {label} ...", end=" ", flush=True)

            wasm, err = compile_one(src)
            if err:
                print("FAIL")
                failures.append((label, err))
                continue

            out_path = os.path.join(args.out, os.path.basename(input_dir.rstrip("/")), rel)[:-3] + ".wasm"
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(wasm)
            print(f"OK ({len(wasm)} bytes)")
            ok += 1

    print("\n" + "=" * 60)
    print(f"SUMMARY: {ok}/{total} compiled to WASM")
    print("=" * 60)
    if failures:
        print(f"\n{len(failures)} failure(s):")
        for label, err in failures:
            print(f"  - {label}: {err}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
