import ast
import os
import sys
import tempfile
import subprocess
import shutil
import uuid

from mycelium_compiler.parser import MyceliumCompilerVisitor
from .inferrer import StorageTypeInferrer
from .transpiler import RustTranspiler, collect_local_vars
from .utils import (
    to_pascal_case,
    escape_keyword,
    map_type,
    check_keyword_usage,
)

def generate_rust_intermediate(visitor: MyceliumCompilerVisitor) -> str:
    """Translates the validated Python AST into Soroban Rust code."""
    lines = ["#![no_std]"]

    # Build use statement
    use_items = [
        "contract", "contractimpl", "Env", "Symbol", "Address", "U256",
        "Map", "Vec", "Bytes", "IntoVal", "Val", "TryFromVal"
    ]
    if visitor.errors:
        use_items.extend(["contracterror", "panic_with_error"])
    lines.append(f"#[allow(unused_imports)]")
    lines.append(f"use soroban_sdk::{{{', '.join(use_items)}}};")
    lines.append("use soroban_sdk::xdr::ToXdr;")
    lines.append("")

    # Generate ContractError enum
    has_errors = bool(visitor.errors and visitor.errors.get("fields"))
    if has_errors:
        lines.append("#[contracterror]")
        lines.append("#[derive(Copy, Clone, Debug, Eq, PartialEq, PartialOrd, Ord)]")
        lines.append("#[repr(u32)]")
        lines.append("pub enum ContractError {")
        for name, value in visitor.errors["fields"].items():
            lines.append(f"    {to_pascal_case(name)} = {value},")
        lines.append("}")
        lines.append("")

    # Generate const class enums (RootStatus, etc.)
    for class_name, variants in visitor.const_classes.items():
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in variants.values()):
            continue
        lines.append("#[derive(Copy, Clone, Debug, Eq, PartialEq)]")
        lines.append("#[repr(u32)]")
        lines.append(f"pub enum {class_name} {{")
        for name, value in variants.items():
            lines.append(f"    {to_pascal_case(name)} = {value},")
        lines.append("}")
        lines.append("")

    lines.append("#[contract]")
    lines.append(f"pub struct {visitor.contract_name};")
    lines.append("")
    lines.append("#[contractimpl]")
    lines.append(f"impl {visitor.contract_name} {{")

    # Global storage type inference across all functions
    global_inferrer = StorageTypeInferrer(
        visitor.state_variables, visitor.functions,
        local_var_types={}, module_constants=visitor.module_constants
    )
    global_inferrer.infer()
    global_storage_key_types = global_inferrer.storage_key_types

    for func in visitor.functions:
        func_node = func.get("node")

        # Skip __init__ constructor
        if func["name"] == "__init__":
            continue

        # Determine visibility
        is_public = True
        if func["name"].startswith("_"):
            is_public = False
        # Check decorators
        if func_node:
            for dec in func_node.decorator_list:
                if isinstance(dec, ast.Name) and dec.id in ('external', 'view'):
                    is_public = True

        pub_str = "pub " if is_public else ""

        # Determine extra parameter injections (backward compat)
        extra_args = []
        if func_node:
            if check_keyword_usage(func_node, "msg_sender"):
                extra_args.append("msg_sender: Address")
            if check_keyword_usage(func_node, "msg_value"):
                extra_args.append("msg_value: U256")

        args_list = ["env: Env"]
        for arg_name, arg_type in func["args"]:
            if arg_name == "self":
                continue
            if arg_type == "Env":
                continue  # env is auto-injected
            safe_name = escape_keyword(arg_name)
            args_list.append(f"{safe_name}: {map_type(arg_type)}")

        args_list.extend(extra_args)
        args_str = ", ".join(args_list)

        ret_type = ""
        mapped_ret_type = None
        if func["returns"] != "None":
            mapped_ret_type = map_type(func['returns'])
            ret_type = f" -> {mapped_ret_type}"

        lines.append(f"    {pub_str}fn {func['name']}({args_str}){ret_type} {{")

        # Inject global emulation bindings (backward compat)
        body_prefix = []
        if func_node:
            if check_keyword_usage(func_node, "msg_sender"):
                body_prefix.append("        msg_sender.require_auth();")
            if check_keyword_usage(func_node, "block_timestamp"):
                body_prefix.append("        let block_timestamp = env.ledger().timestamp();")
            if check_keyword_usage(func_node, "block_number"):
                body_prefix.append("        let block_number = env.ledger().sequence() as u64;")
            if check_keyword_usage(func_node, "ZERO_ADDRESS"):
                body_prefix.append('        let ZERO_ADDRESS = Address::from_string(&soroban_sdk::String::from_str(&env, "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"));')
            if check_keyword_usage(func_node, "self_balance"):
                body_prefix.append("        let self_balance = U256::from_u32(&env, 1000000);")

        local_var_types = {}
        inferred_locals = global_inferrer.func_local_types.get(func["name"], {})
        for k, v in inferred_locals.items():
            local_var_types[k] = map_type(v)

        for arg_name, arg_type in func["args"]:
            if arg_name != "self":
                local_var_types[arg_name] = map_type(arg_type)
        if func_node:
            if check_keyword_usage(func_node, "msg_sender"):
                local_var_types["msg_sender"] = "Address"
            if check_keyword_usage(func_node, "msg_value"):
                local_var_types["msg_value"] = "U256"
            if check_keyword_usage(func_node, "block_timestamp"):
                local_var_types["block_timestamp"] = "u64"
            if check_keyword_usage(func_node, "block_number"):
                local_var_types["block_number"] = "u64"
            if check_keyword_usage(func_node, "ZERO_ADDRESS"):
                local_var_types["ZERO_ADDRESS"] = "Address"
            if check_keyword_usage(func_node, "self_balance"):
                local_var_types["self_balance"] = "U256"

        # If the function returns a Vec<T> and the function returns a local
        # variable by name (e.g. `return matched_agents`), prefer the
        # function's return Vec element type for that local variable. This
        # fixes cases where `Vec()` is created without explicit element type
        # and thus defaults to Vec<Val>.
        if mapped_ret_type and mapped_ret_type.startswith("soroban_sdk::Vec<") and func_node is not None:
            ret_var_name = None
            for n in ast.walk(func_node):
                if isinstance(n, ast.Return) and isinstance(n.value, ast.Name):
                    ret_var_name = n.value.id
                    break
            if ret_var_name:
                # override or set the local var type for the returned container
                local_var_types[ret_var_name] = mapped_ret_type

        # Collect local variables to pre-declare
        local_vars_to_declare = collect_local_vars(func_node)
        
        # Exclude arguments
        args_names = {arg_name for arg_name, _ in func["args"]}
        local_vars_to_declare = local_vars_to_declare - args_names
        
        # Exclude injected variables
        injected = {"self", "env", "msg_sender", "msg_value", "block_timestamp", "block_number", "ZERO_ADDRESS", "self_balance"}
        local_vars_to_declare = local_vars_to_declare - injected
        
        # Exclude module constants
        local_vars_to_declare = local_vars_to_declare - set(visitor.module_constants.keys())

        transpiler = RustTranspiler(
            visitor.state_variables, visitor.contract_name, visitor.events,
            local_var_types, return_type=mapped_ret_type,
            functions_meta=visitor.functions, has_errors=has_errors,
            storage_key_types=global_storage_key_types,
            const_classes=visitor.const_classes,
            module_constants=visitor.module_constants,
            func_node=func_node
        )
        
        # Seed local_vars so that assignments do not declare 'let mut' inside blocks
        transpiler.local_vars.update(local_vars_to_declare)
        
        body_lines = []
        if func_node and hasattr(func_node, "body"):
            for stmt in func_node.body:
                body_lines.append("        " + transpiler.transpile_stmt(stmt))
        else:
            body_lines.append("        // Default return fallback")

        # Generate variable declarations with inferred types
        declarations = []
        for var_name in sorted(list(local_vars_to_declare)):
            safe_name = escape_keyword(var_name)
            var_type = transpiler.local_var_types.get(var_name)
            if var_type:
                declarations.append(f"        let mut {safe_name}: {var_type};")
            else:
                declarations.append(f"        let mut {safe_name};")

        lines.extend(body_prefix)
        lines.extend(declarations)
        lines.extend(body_lines)
        lines.append("    }")

    lines.append("}")
    return "\n".join(lines)


# ─── Stellar CLI Bootstrapper ─────────────────────────────────────────────

def ensure_stellar_cli() -> str:
    """
    Checks if 'stellar' CLI is available in system PATH or downloads it automatically.
    Returns the path to the stellar binary.
    """
    import platform
    import urllib.request
    import tarfile

    system = platform.system().lower()
    machine = platform.machine().lower()

    stellar_bin_name = "stellar.exe" if "windows" in system else "stellar"

    system_stellar = shutil.which("stellar")
    if system_stellar:
        return system_stellar

    current_dir = os.path.dirname(os.path.abspath(__file__))
    local_bin_dir = os.path.join(current_dir, "bin")
    local_stellar = os.path.join(local_bin_dir, stellar_bin_name)
    if os.path.exists(local_stellar):
        return local_stellar

    os.makedirs(local_bin_dir, exist_ok=True)

    version = "27.0.0"

    if "windows" in system and ("86" in machine or "amd64" in machine):
        filename = f"stellar-cli-{version}-x86_64-pc-windows-msvc.zip"
    elif "linux" in system and "86" in machine:
        filename = f"stellar-cli-{version}-x86_64-unknown-linux-gnu.tar.gz"
    elif "darwin" in system or "mac" in system:
        if "arm" in machine or "aarch" in machine:
            filename = f"stellar-cli-{version}-aarch64-apple-darwin.tar.gz"
        else:
            filename = f"stellar-cli-{version}-x86_64-apple-darwin.tar.gz"
    else:
        print(f"[Stellar CLI Bootstrapper] Unsupported platform ({system}) / architecture ({machine}) for auto-download. Using fallback 'stellar'.")
        return "stellar"

    url = f"https://github.com/stellar/stellar-cli/releases/download/v{version}/{filename}"

    print(f"[Stellar CLI Bootstrapper] Downloading stellar-cli v{version} from {url}...")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = os.path.join(tmpdir, filename)
            urllib.request.urlretrieve(url, archive_path)

            if filename.endswith(".tar.gz"):
                with tarfile.open(archive_path, "r:gz") as tar:
                    tar.extractall(path=tmpdir)
            elif filename.endswith(".zip"):
                import zipfile
                with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                    zip_ref.extractall(tmpdir)

            found_path = None
            for root, dirs, files in os.walk(tmpdir):
                if stellar_bin_name in files:
                    found_path = os.path.join(root, stellar_bin_name)
                    break

            if found_path and os.path.exists(found_path):
                shutil.copy2(found_path, local_stellar)
                if "windows" not in system:
                    os.chmod(local_stellar, 0o755)
                print(f"[Stellar CLI Bootstrapper] Successfully installed stellar-cli at {local_stellar}")
                return local_stellar
            else:
                raise FileNotFoundError(f"Could not find '{stellar_bin_name}' binary in extracted archive")

    except Exception as e:
        print(f"[Stellar CLI Bootstrapper] Failed to download or install stellar-cli: {e}. Using fallback 'stellar'.")
        return "stellar"


def generate_wasm(visitor: MyceliumCompilerVisitor) -> bytes:
    """
    Generate a valid Soroban-compatible WASM binary from parsed contract AST
    by transpiling to Soroban Rust and invoking cargo/stellar CLI.
    """
    rust_code = generate_rust_intermediate(visitor)

    static_workspace = "/app/mycelium_contract_workspace"
    is_static = False
    if os.path.exists(static_workspace):
        temp_dir = static_workspace
        is_static = True
    else:
        temp_dir = os.path.join(tempfile.gettempdir(), f"mycelium_compile_{uuid.uuid4()}")

    os.makedirs(os.path.join(temp_dir, "src"), exist_ok=True)

    cargo_toml = """[package]
name = "mycelium_contract"
version = "0.1.0"
edition = "2021"

[lib]
crate-type = ["cdylib"]

[dependencies]
soroban-sdk = "26.1.0"

[profile.release]
opt-level = "z"
overflow-checks = true
lto = true
codegen-units = 1
panic = "abort"
"""

    try:
        with open(os.path.join(temp_dir, "Cargo.toml"), "w") as f:
            f.write(cargo_toml)

        with open(os.path.join(temp_dir, "src", "lib.rs"), "w") as f:
            f.write(rust_code)

        stellar_bin = ensure_stellar_cli()

        cmd = [stellar_bin, "contract", "build", "--manifest-path", "Cargo.toml"]

        cache_dir = "/app/cargo_target"
        if os.path.exists(cache_dir):
            target_dir = cache_dir
        else:
            target_dir = "/tmp/mycelium_cargo_target"
            os.makedirs(target_dir, exist_ok=True)

        env = os.environ.copy()
        env["CARGO_TARGET_DIR"] = target_dir
        env["CARGO_NET_OFFLINE"] = "true"

        print(f"DEBUG: Executing cmd: {cmd} in cwd: {temp_dir}", file=sys.stderr, flush=True)
        print(f"DEBUG: CARGO_TARGET_DIR={env.get('CARGO_TARGET_DIR')}", file=sys.stderr, flush=True)
        print(f"DEBUG: CARGO_NET_OFFLINE={env.get('CARGO_NET_OFFLINE')}", file=sys.stderr, flush=True)

        res = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=temp_dir)

        print(f"--- Cargo STDOUT ---\n{res.stdout}", file=sys.stderr)
        print(f"--- Cargo STDERR ---\n{res.stderr}", file=sys.stderr)

        if res.returncode != 0:
            error_log = f"Rust Compilation Error:\n{res.stderr}\n{res.stdout}"
            print(error_log, file=sys.stderr)
            raise RuntimeError(error_log)

        wasm_path = os.path.join(target_dir, "wasm32v1-none", "release", "mycelium_contract.wasm")

        if not os.path.exists(wasm_path):
            wasm_path = os.path.join(target_dir, "wasm32-unknown-unknown", "release", "mycelium_contract.wasm")

        if not os.path.exists(wasm_path):
            raise FileNotFoundError(f"Compiled WASM not found in target directories of {target_dir}")

        with open(wasm_path, "rb") as f_wasm:
            wasm_bytes = f_wasm.read()

        return wasm_bytes

    finally:
        if 'is_static' in locals() and not is_static and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
