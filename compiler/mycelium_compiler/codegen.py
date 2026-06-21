import struct
import ast
import os
import sys
import tempfile
import subprocess
import shutil
import uuid
from mycelium_compiler.parser import MyceliumCompilerVisitor

# Rust reserved keywords that cannot be used as identifiers
RUST_KEYWORDS = {
    'as', 'break', 'const', 'continue', 'crate', 'else', 'enum', 'extern',
    'false', 'fn', 'for', 'if', 'impl', 'in', 'let', 'loop', 'match', 'mod',
    'move', 'mut', 'pub', 'ref', 'return', 'self', 'Self', 'static', 'struct',
    'super', 'trait', 'true', 'type', 'unsafe', 'use', 'where', 'while',
    'async', 'await', 'dyn', 'abstract', 'become', 'box', 'do', 'final',
    'macro', 'override', 'priv', 'typeof', 'unsized', 'virtual', 'yield', 'try',
}

def escape_keyword(name: str) -> str:
    """Escape Rust reserved keywords with r# prefix."""
    if name in RUST_KEYWORDS:
        return f'r#{name}'
    return name


def to_pascal_case(name: str) -> str:
    """Convert UPPER_SNAKE_CASE to PascalCase. E.g. NOT_INITIALIZED -> NotInitialized"""
    return ''.join(word.capitalize() for word in name.lower().split('_'))


def map_type(type_str: str) -> str:
    type_str = type_str.strip()
    if type_str.endswith(" or None"):
        base_mapped = map_type(type_str[:-8])
        return f"Option<{base_mapped}>"

    if type_str == "uint256" or type_str == "u256" or type_str == "int" or type_str == "U256":
        return "U256"
    elif type_str == "address" or type_str == "Address":
        return "Address"
    elif type_str == "bytes32":
        return "soroban_sdk::BytesN<32>"
    elif type_str == "String" or type_str == "Symbol":
        return "Symbol"
    elif type_str == "bool" or type_str == "Bool":
        return "bool"
    elif type_str == "U128":
        return "u128"
    elif type_str == "U64":
        return "u64"
    elif type_str == "U32":
        return "u32"
    elif type_str == "I128":
        return "i128"
    elif type_str == "I32":
        return "i32"
    elif type_str == "Env":
        return "Env"
    elif type_str == "str":
        return "Symbol"
    elif type_str == "list":
        return "soroban_sdk::Vec<soroban_sdk::Val>"
    elif type_str == "tuple":
        return "(soroban_sdk::Val, soroban_sdk::Val)"

    if type_str.startswith("indexed(") and type_str.endswith(")"):
        return map_type(type_str[8:-1])

    if type_str.startswith("Bytes[") and type_str.endswith("]"):
        return "soroban_sdk::Bytes"

    if type_str.startswith("DynArray[") and type_str.endswith("]"):
        inner = type_str[9:-1]
        parts = [p.strip() for p in inner.rsplit(",", 1)]
        return f"soroban_sdk::Vec<{map_type(parts[0])}>"

    if type_str.startswith("Vec[") and type_str.endswith("]"):
        inner = type_str[4:-1]
        return f"soroban_sdk::Vec<{map_type(inner)}>"

    if type_str.startswith("List[") and type_str.endswith("]"):
        inner = type_str[5:-1]
        return f"soroban_sdk::Vec<{map_type(inner)}>"

    if type_str.startswith("list[") and type_str.endswith("]"):
        inner = type_str[5:-1]
        return f"soroban_sdk::Vec<{map_type(inner)}>"

    if type_str.startswith("Mapping[") and type_str.endswith("]"):
        inner = type_str[8:-1]
        parts = [p.strip() for p in inner.split(",", 1)]
        return f"soroban_sdk::Map<{map_type(parts[0])}, {map_type(parts[1])}>"

    if type_str.startswith("Map[") and type_str.endswith("]"):
        inner = type_str[4:-1]
        parts = [p.strip() for p in inner.split(",", 1)]
        return f"soroban_sdk::Map<{map_type(parts[0])}, {map_type(parts[1])}>"

    if type_str.startswith("dict[") and type_str.endswith("]"):
        inner = type_str[5:-1]
        parts = [p.strip() for p in inner.split(",", 1)]
        return f"soroban_sdk::Map<{map_type(parts[0])}, {map_type(parts[1])}>"

    if type_str == "Bytes":
        return "soroban_sdk::Bytes"

    if type_str == "Map":
        return "soroban_sdk::Map<soroban_sdk::Val, soroban_sdk::Val>"

    if type_str == "Vec":
        return "soroban_sdk::Vec<soroban_sdk::Val>"

    if type_str.startswith("(") and type_str.endswith(")"):
        inner = type_str[1:-1]
        parts = [map_type(p.strip()) for p in inner.split(",")]
        return f"({', '.join(parts)})"

    return type_str



def get_subscript_type(type_str: str, num_keys: int) -> str:
    type_str = type_str.strip()
    for _ in range(num_keys):
        if type_str.startswith("Mapping[") and type_str.endswith("]"):
            inner = type_str[8:-1]
            parts = [p.strip() for p in inner.split(",", 1)]
            type_str = parts[1]
        elif type_str.startswith("Map[") and type_str.endswith("]"):
            inner = type_str[4:-1]
            parts = [p.strip() for p in inner.split(",", 1)]
            type_str = parts[1]
        elif type_str.startswith("dict[") and type_str.endswith("]"):
            inner = type_str[5:-1]
            parts = [p.strip() for p in inner.split(",", 1)]
            type_str = parts[1]
        else:
            break
    return type_str


def flatten_subscript(node):
    keys = []
    curr = node
    while isinstance(curr, ast.Subscript):
        keys.insert(0, curr.slice)
        curr = curr.value
    if isinstance(curr, ast.Attribute) and isinstance(curr.value, ast.Name) and curr.value.id == 'self':
        return curr.attr, keys
    elif isinstance(curr, ast.Name):
        return curr.id, keys
    return None, []


def check_keyword_usage(func_node, keyword):
    for node in ast.walk(func_node):
        if isinstance(node, ast.Name) and node.id == keyword:
            return True
    return False


# ─── Storage Type Inference ───────────────────────────────────────────────

class StorageTypeInferrer(ast.NodeVisitor):
    """Infers Rust types for storage keys from self.storage.set() calls."""

    def __init__(self, state_variables, functions_meta, local_var_types=None, module_constants=None):
        self.state_variables = state_variables
        self.functions_meta = functions_meta
        self.storage_key_types = {}  # key_pattern -> rust_type_str
        self.local_var_types = local_var_types or {}
        self.local_var_exprs = {}  # var_name -> original ast node for key pattern resolution
        self.module_constants = module_constants or {}
        self.func_local_types = {}

    def infer(self):
        for func in self.functions_meta:
            func_node = func.get("node")
            if func_node:
                self.local_var_types = {}
                self.local_var_exprs = {}
                for arg_name, arg_type in func["args"]:
                    if arg_name != "self":
                        self.local_var_types[arg_name] = arg_type
                self.visit(func_node)
                self.func_local_types[func["name"]] = dict(self.local_var_types)

    def _extract_key_pattern(self, node):
        """Extract a string key pattern from a key expression node."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    parts.append(v.value)
                elif isinstance(v, ast.FormattedValue):
                    parts.append("{}")
            return "".join(parts)
        if isinstance(node, ast.Tuple):
            elts = [self._extract_key_pattern(e) for e in node.elts]
            return tuple(elts)
        if isinstance(node, ast.Name):
            # Look up the original expression for this variable
            if node.id in self.local_var_exprs:
                return self._extract_key_pattern(self.local_var_exprs[node.id])
            return None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._extract_key_pattern(node.left)
            right = self._extract_key_pattern(node.right)
            if left and right:
                return left + right
            return left or right
        return None

    def _infer_type_from_expr(self, node):
        """Infer Rust type string from a value expression node."""
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return "bool"
            elif isinstance(node.value, int):
                return "u128"
            elif isinstance(node.value, str):
                return "Symbol"
            elif isinstance(node.value, bytes):
                return "soroban_sdk::Bytes"
            return None
        if isinstance(node, ast.Name):
            if node.id in ('True', 'False'):
                return "bool"
            if node.id in self.local_var_types:
                return map_type(self.local_var_types[node.id])
            if node.id in self.state_variables:
                return map_type(self.state_variables[node.id].get("type", ""))
            if node.id in self.module_constants:
                val = self.module_constants[node.id]
                if isinstance(val, bool): return "bool"
                if isinstance(val, int): return "u128"
                if isinstance(val, str): return "Symbol"
                if isinstance(val, bytes): return "soroban_sdk::Bytes"
            return None
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                name = node.func.id
                if name == "U128": return "u128"
                if name == "U64": return "u64"
                if name == "U32": return "u32"
                if name == "I128": return "i128"
                if name == "I32": return "i32"
                if name == "Bool": return "bool"
                if name == "Address": return "Address"
                if name == "Symbol": return "Symbol"
                if name == "Bytes": return "soroban_sdk::Bytes"
                if name == "Map": return "soroban_sdk::Map<soroban_sdk::Val, soroban_sdk::Val>"
                if name == "Vec": return "soroban_sdk::Vec<soroban_sdk::Val>"
            if isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name):
                    if node.func.value.id == "Address" and node.func.attr == "from_string":
                        return "Address"
                    if node.func.value.id in self.local_var_types:
                        base_type = map_type(self.local_var_types[node.func.value.id])
                        if node.func.attr == 'get':
                            if base_type.startswith("soroban_sdk::Vec<") and base_type.endswith(">"):
                                return base_type[17:-1]
                            elif base_type.startswith("soroban_sdk::Map<") and base_type.endswith(">"):
                                parts = base_type[17:-1].split(",")
                                if len(parts) == 2:
                                    return parts[1].strip()
                        return base_type
        if isinstance(node, ast.List):
            if not node.elts:
                return "soroban_sdk::Vec<soroban_sdk::Val>"
            elem_types = [self._infer_type_from_expr(e) for e in node.elts]
            inner = elem_types[0] if elem_types[0] else "soroban_sdk::Val"
            return f"soroban_sdk::Vec<{inner}>"
        if isinstance(node, ast.Dict):
            return "soroban_sdk::Map<soroban_sdk::Val, soroban_sdk::Val>"
        if isinstance(node, ast.Tuple):
            elts = [self._infer_type_from_expr(e) for e in node.elts]
            return f"({', '.join(e for e in elts if e)})"
        if isinstance(node, ast.BinOp):
            left_type = self._infer_type_from_expr(node.left)
            right_type = self._infer_type_from_expr(node.right)
            if left_type and right_type and left_type == right_type:
                return left_type
            return left_type or right_type or "u128"
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "self":
                if node.attr in self.state_variables:
                    return map_type(self.state_variables[node.attr].get("type", ""))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            # Handle self.env.serialize(...) -> soroban_sdk::Bytes
            if (isinstance(node.func.value, ast.Attribute) and
                node.func.value.attr == 'env' and
                isinstance(node.func.value.value, ast.Name) and
                node.func.value.value.id == 'self' and
                node.func.attr == 'serialize'):
                return "soroban_sdk::Bytes"
            # Handle self.storage.get(key) and self.storage.get(key, default) 
            if (isinstance(node.func.value, ast.Attribute) and
                node.func.value.attr == 'storage' and
                isinstance(node.func.value.value, ast.Name) and
                node.func.value.value.id == 'self' and
                node.func.attr == 'get' and len(node.args) >= 1):
                # Look up key type from inferred storage types
                key_pat = self._extract_key_pattern(node.args[0])
                if key_pat and key_pat in self.storage_key_types:
                    return self.storage_key_types[key_pat]
                if len(node.args) >= 2:
                    return self._infer_type_from_expr(node.args[1])
                return None
            # Handle env.ledger().timestamp() -> u64
            if (isinstance(node.func, ast.Attribute) and
                node.func.attr in ('timestamp', 'sequence') and
                isinstance(node.func.value, ast.Call) and
                isinstance(node.func.value.func, ast.Attribute) and
                node.func.value.func.attr == 'ledger' and
                isinstance(node.func.value.func.value, ast.Name) and
                node.func.value.func.value.id == 'env'):
                return "u64"
        return None

    def _infer_type_from_value_call(self, node):
        """Track type changes from method calls like .append() to infer Vec type."""
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr == 'append':
                base = node.func.value
                base_type = self._infer_type_from_expr(base)
                if base_type and base_type.startswith("soroban_sdk::Vec<"):
                    return base_type
                if base_type:
                    return f"soroban_sdk::Vec<{base_type}>"
        return None

    def visit_Assign(self, node):
        """Track local variable types from dict/constructor assignments."""
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            inferred = self._infer_type_from_expr(node.value)
            if inferred:
                self.local_var_types[node.targets[0].id] = inferred
            # Also track the expression for key pattern resolution
            self.local_var_exprs[node.targets[0].id] = node.value

    def visit_Call(self, node):
        """Detect self.storage.set(key, value) calls and infer types."""
        if isinstance(node.func, ast.Attribute):
            val = node.func.value
            if (isinstance(val, ast.Attribute) and val.attr == 'storage' and
                isinstance(val.value, ast.Name) and val.value.id == 'self' and
                node.func.attr == 'set' and len(node.args) >= 2):
                key_node = node.args[0]
                value_node = node.args[1]
                key_pattern = self._extract_key_pattern(key_node)
                if key_pattern:
                    value_type = self._infer_type_from_expr(value_node)
                    if value_type:
                        self.storage_key_types[key_pattern] = value_type

            # Track .append() on storage.get results to update Vec types
            if (isinstance(node.func, ast.Attribute) and node.func.attr == 'append'):
                base = node.func.value
                original_base_name = base.id if isinstance(base, ast.Name) else None
                if isinstance(base, ast.Name):
                    base_expr = self.local_var_exprs.get(base.id)
                    if base_expr:
                        base = base_expr
                if isinstance(base, ast.Call):
                    if (isinstance(base.func, ast.Attribute) and
                        base.func.attr == 'get' and
                        isinstance(base.func.value, ast.Attribute) and
                        base.func.value.attr == 'storage' and
                        isinstance(base.func.value.value, ast.Name) and
                        base.func.value.value.id == 'self' and
                        len(base.args) >= 1):
                        key_node = base.args[0]
                        key_pattern = self._extract_key_pattern(key_node)
                        if key_pattern and key_pattern in self.storage_key_types:
                            current = self.storage_key_types[key_pattern]
                            if not current.startswith("soroban_sdk::Vec<") or "Val" in current:
                                arg_type = self._infer_type_from_expr(node.args[0]) if node.args else None
                                if arg_type and "Val" not in arg_type:
                                    new_type = f"soroban_sdk::Vec<{arg_type}>"
                                    self.storage_key_types[key_pattern] = new_type
                                    if original_base_name:
                                        self.local_var_types[original_base_name] = new_type
                elif original_base_name and original_base_name in self.local_var_types:
                    current = self.local_var_types[original_base_name]
                    if current:
                        mapped_curr = map_type(current)
                        if "Val" in mapped_curr or mapped_curr.startswith("soroban_sdk::Vec<Val>") or mapped_curr == "soroban_sdk::Vec<soroban_sdk::Val>":
                            arg_type = self._infer_type_from_expr(node.args[0]) if node.args else None
                            if arg_type and "Val" not in arg_type:
                                self.local_var_types[original_base_name] = f"soroban_sdk::Vec<{arg_type}>"

        self.generic_visit(node)


# ─── Transpiler ───────────────────────────────────────────────────────────

class RustTranspiler(ast.NodeVisitor):
    def __init__(self, state_variables, contract_name, events, local_var_types=None,
                 return_type=None, functions_meta=None, has_errors=False,
                 storage_key_types=None, const_classes=None, module_constants=None):
        self.state_variables = state_variables
        self.contract_name = contract_name
        self.events = events
        self.local_vars = set()
        self.local_var_types = local_var_types or {}
        self.return_type = return_type
        self.functions_meta = functions_meta or []
        self.has_errors = has_errors
        self.storage_key_types = storage_key_types or {}
        self.const_classes = const_classes or {}
        self.local_var_exprs = {}  # var_name -> original AST node
        self.option_vars = {}  # var_name -> inner_type for Option-typed variables
        self.module_constants = module_constants or {}

    # ── Pattern detection helpers ──────────────────────────────────────

    def _is_self_storage_call(self, node):
        """Return method name if node is self.storage.METHOD(...), else None."""
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            val = node.func.value
            if isinstance(val, ast.Attribute) and val.attr == 'storage':
                if isinstance(val.value, ast.Name) and val.value.id == 'self':
                    return node.func.attr
        return None

    def _is_self_env_call(self, node):
        """Return method name if node is self.env.METHOD(...), else None."""
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            val = node.func.value
            if isinstance(val, ast.Attribute) and val.attr == 'env':
                if isinstance(val.value, ast.Name) and val.value.id == 'self':
                    return node.func.attr
        return None

    def _get_self_env_chain(self, node):
        """Detect self.env.X().Y(...) chains. Returns (X, Y) or None."""
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            outer_method = node.func.attr
            inner_call = node.func.value
            if isinstance(inner_call, ast.Call) and isinstance(inner_call.func, ast.Attribute):
                inner_method = inner_call.func.attr
                inner_val = inner_call.func.value
                if isinstance(inner_val, ast.Attribute) and inner_val.attr == 'env':
                    if isinstance(inner_val.value, ast.Name) and inner_val.value.id == 'self':
                        return (inner_method, outer_method)
        return None

    # ── Storage key helpers ────────────────────────────────────────────

    def _extract_key_parts(self, node):
        """Recursively extract parts of a composite storage key expression."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value.replace(':', '_').strip('_')
            if s:
                return [f'Symbol::new(&env, "{s}")']
            return []
        elif isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant):
                    s = str(v.value).replace(':', '_').strip('_')
                    if s:
                        parts.append(f'Symbol::new(&env, "{s}")')
                elif isinstance(v, ast.FormattedValue):
                    expr = self.transpile_expr(v.value)
                    if expr.endswith('.clone()'):
                        parts.append(expr)
                    else:
                        parts.append(f'{expr}.clone()')
            return parts
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left_parts = self._extract_key_parts(node.left)
            right_parts = self._extract_key_parts(node.right)
            return left_parts + right_parts
        else:
            expr = self.transpile_expr(node)
            if expr.endswith('.clone()'):
                return [expr]
            return [f'{expr}.clone()']

    def _is_val_typed(self, node):
        """Check if an expression node has type containing Val (bare Map/Vec)."""
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in ('Map', 'Vec') and not node.args:
                return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get':
            base_type = self.get_expr_type(node.func.value)
            if base_type and "Val" in base_type:
                return True
        if isinstance(node, ast.Subscript):
            return self._is_val_typed(node.value)
        if isinstance(node, ast.Name):
            var_type = self.local_var_types.get(node.id, "")
            if "soroban_sdk::Val" in var_type:
                return True
        return False

    def _is_vec_typed(self, node):
        """Check if an expression node has type Vec."""
        if isinstance(node, ast.Name):
            var_type = self.local_var_types.get(node.id, "")
            return var_type.startswith("soroban_sdk::Vec") or var_type.startswith("soroban_sdk::Vec<")
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == 'self':
                var_type = self.state_variables.get(node.attr, {}).get("type", "")
                mapped = map_type(var_type)
                return mapped.startswith("soroban_sdk::Vec") or mapped.startswith("soroban_sdk::Vec<")
        if isinstance(node, ast.Call):
            ret_type = self.get_expr_type(node)
            if ret_type:
                return ret_type.startswith("soroban_sdk::Vec") or ret_type.startswith("soroban_sdk::Vec<")
        return False

    def _extract_key_pattern(self, node):
        """Extract a string key pattern from a key expression node."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    parts.append(v.value)
                elif isinstance(v, ast.FormattedValue):
                    parts.append("{}")
            return "".join(parts)
        if isinstance(node, ast.Tuple):
            elts = [self._extract_key_pattern(e) for e in node.elts]
            return tuple(elts)
        if isinstance(node, ast.Name):
            if hasattr(self, 'local_var_exprs') and node.id in self.local_var_exprs:
                return self._extract_key_pattern(self.local_var_exprs[node.id])
            return None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._extract_key_pattern(node.left)
            right = self._extract_key_pattern(node.right)
            if left and right:
                return left + right
            return left or right
        return None

    def _get_storage_key_type(self, node):
        """Look up inferred Rust type for a storage key expression."""
        pat = self._extract_key_pattern(node)
        if pat and pat in self.storage_key_types:
            return self.storage_key_types[pat]
        if isinstance(node, ast.Name):
            if hasattr(self, 'local_var_exprs') and node.id in self.local_var_exprs:
                return self._get_storage_key_type(self.local_var_exprs[node.id])
            return self.storage_key_types.get(node.id)
        return None

    def _transpile_storage_key(self, node):
        """Transpile a storage key expression to a Rust &key expression."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return f'&Symbol::new(&env, "{node.value}")'
        elif isinstance(node, ast.Tuple):
            parts = []
            for elt in node.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    parts.append(f'Symbol::new(&env, "{elt.value}")')
                else:
                    expr = self.transpile_expr(elt)
                    if expr.endswith('.clone()'):
                        parts.append(expr)
                    else:
                        parts.append(f'{expr}.clone()')
            return f'&({", ".join(parts)})'
        elif isinstance(node, ast.JoinedStr) or (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)):
            parts = self._extract_key_parts(node)
            if len(parts) == 0:
                return '&Symbol::new(&env, "_")'
            if len(parts) == 1:
                return f'&{parts[0]}'
            return f'&({", ".join(parts)})'
        else:
            expr = self.transpile_expr(node)
            return f'&{expr}'

    # ── Function return type lookup ────────────────────────────────────

    def _get_function_return_type(self, func_name):
        for f in self.functions_meta:
            if f['name'] == func_name:
                ret = f.get('returns', 'None')
                if ret != 'None':
                    return map_type(ret)
        return None

    # ── U256 helpers (backward compat) ─────────────────────────────────

    def _coerce_to_u256(self, node):
        expr_str = self.transpile_expr(node)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return f"U256::from_u32(&env, {node.value})"
        if isinstance(node, (ast.BinOp, ast.Call, ast.BoolOp, ast.Compare, ast.IfExp)):
            return f"U256::from_u128(&env, ({expr_str}) as u128)"
        return f"U256::from_u128(&env, {expr_str} as u128)"

    def is_u256_type(self, node):
        if isinstance(node, ast.Name):
            if node.id in self.local_var_types:
                return self.local_var_types[node.id] == "U256"
            if node.id in self.state_variables:
                return map_type(self.state_variables[node.id].get("type", "")) == "U256"
            if node.id in ("msg_value", "self_balance"):
                return True
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == 'self':
                var_name = node.attr
                if var_name in self.state_variables:
                    return map_type(self.state_variables[var_name].get("type", "")) == "U256"
        elif isinstance(node, ast.Subscript):
            attr, keys = flatten_subscript(node)
            if attr and attr in self.state_variables:
                var_info = self.state_variables[attr]
                var_type = var_info.get("type", "")
                leaf_type = get_subscript_type(var_type, len(keys))
                return map_type(leaf_type) == "U256"
            elif attr and attr in self.local_var_types:
                var_type = self.local_var_types[attr]
                if var_type.startswith("soroban_sdk::Vec<") and var_type.endswith(">"):
                    return var_type[17:-1] == "U256"
                elif var_type.startswith("soroban_sdk::Map<") and var_type.endswith(">"):
                    parts = var_type[17:-1].split(",")
                    if len(parts) == 2:
                        return parts[1].strip() == "U256"
        elif isinstance(node, ast.BinOp):
            return self.is_u256_type(node.left) or self.is_u256_type(node.right)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == 'self':
                ret_type = self._get_function_return_type(node.func.attr)
                return ret_type == "U256"
        return False

    # ── Expression transpilation ───────────────────────────────────────

    def transpile_expr(self, node, coerce_to=None):
        if isinstance(node, ast.Name):
            if node.id == 'True':
                return 'true'
            elif node.id == 'False':
                return 'false'
            elif node.id == 'None':
                return 'None'
            if node.id in self.module_constants:
                val = self.module_constants[node.id]
                if isinstance(val, bool):
                    return 'true' if val else 'false'
                elif isinstance(val, (int, float)):
                    return str(val)
                elif isinstance(val, str):
                    return f'Symbol::new(&env, "{val}")'
                elif isinstance(val, bytes):
                    try:
                        decoded = val.decode('utf-8')
                        escaped = decoded.replace('"', '\\"')
                        return f'b"{escaped}"'
                    except UnicodeDecodeError:
                        escaped = "".join(f"\\x{b:02x}" for b in val)
                        return f'b"{escaped}"'
            name = escape_keyword(node.id)
            if node.id != "env" and node.id in self.local_var_types:
                t = self.local_var_types[node.id]
                if t not in ("bool", "u32", "u64", "i32", "i64", "i128", "u128", "u8", "u16", "i8", "i16", "soroban_sdk::Val", "Val", "()"):
                    return f"{name}.clone()"
            return name

        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return 'true' if node.value else 'false'
            elif node.value is None:
                return 'None'
            elif isinstance(node.value, (int, float)):
                if coerce_to == 'U256':
                    return f'U256::from_u32(&env, {node.value})'
                return str(node.value)
            elif isinstance(node.value, str):
                return f'Symbol::new(&env, "{node.value}")'
            elif isinstance(node.value, bytes):
                try:
                    decoded = node.value.decode('utf-8')
                    escaped = decoded.replace('"', '\\"')
                    return f'b"{escaped}"'
                except UnicodeDecodeError:
                    escaped = "".join(f"\\x{b:02x}" for b in node.value)
                    return f'b"{escaped}"'
            return str(node.value)

        elif isinstance(node, ast.Tuple):
            elements = [self.transpile_expr(elt) for elt in node.elts]
            return f"({', '.join(elements)})"

        elif isinstance(node, ast.List):
            if not node.elts:
                return 'soroban_sdk::Vec::new(&env)'
            elements = [self.transpile_expr(elt) for elt in node.elts]
            return f"soroban_sdk::vec![&env, {', '.join(elements)}]"

        elif isinstance(node, ast.Dict):
            if not node.keys:
                return 'soroban_sdk::Map::new(&env)'
            parts = []
            parts.append("{\n")
            parts.append("            let mut __map: soroban_sdk::Map<soroban_sdk::Val, soroban_sdk::Val> = soroban_sdk::Map::new(&env);\n")
            for k, v in zip(node.keys, node.values):
                key_str = self.transpile_expr(k)
                val_str = self.transpile_expr(v)
                parts.append(f"            __map.set({key_str}.into_val(&env), {val_str}.into_val(&env));\n")
            parts.append("            __map\n")
            parts.append("        }")
            return "".join(parts)

        elif isinstance(node, ast.JoinedStr):
            # f-string: build a tuple key
            parts = self._extract_key_parts(node)
            if len(parts) == 0:
                return 'Symbol::new(&env, "_")'
            if len(parts) == 1:
                return parts[0]
            return f"({', '.join(parts)})"

        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == 'self':
                var_name = node.attr
                if var_name == 'env':
                    return 'env'
                if var_name == 'storage':
                    return 'env.storage().instance()'
                # Old-style state variable access
                var_info = self.state_variables.get(var_name, {})
                var_type = var_info.get("type", "Symbol")
                rust_type = map_type(var_type)
                get_expr = f'env.storage().instance().get::<_, {rust_type}>(&Symbol::new(&env, "{var_name}"))'
                if rust_type == "U256":
                    return f"{get_expr}.unwrap_or_else(|| U256::from_u32(&env, 0))"
                elif rust_type in ("i128", "bool", "u64", "u32", "i64", "i32", "u128"):
                    return f"{get_expr}.unwrap_or_default()"
                else:
                    return f"{get_expr}.unwrap()"
            else:
                # Check if this is a const class attribute (RootStatus.SUBMITTED)
                if isinstance(node.value, ast.Name) and node.value.id in self.const_classes:
                    return f"({node.value.id}::{to_pascal_case(node.attr)} as u32)"
                return f"{self.transpile_expr(node.value)}.{node.attr}"

        elif isinstance(node, ast.Subscript):
            attr, keys = flatten_subscript(node)
            if attr and attr in self.state_variables:
                var_info = self.state_variables[attr]
                var_type = var_info.get("type", "Symbol")
                leaf_type = get_subscript_type(var_type, len(keys))
                leaf_rust_type = map_type(leaf_type)
                transpiled_keys = []
                for k in keys:
                    k_str = self.transpile_expr(k)
                    if k_str.endswith(".clone()"):
                        transpiled_keys.append(k_str)
                    else:
                        transpiled_keys.append(f"{k_str}.clone()")
                key_tuple_elements = [f'Symbol::new(&env, "{attr}")'] + transpiled_keys
                key_tuple_str = f"({', '.join(key_tuple_elements)},)" if len(key_tuple_elements) == 1 else f"({', '.join(key_tuple_elements)})"
                get_expr = f"env.storage().instance().get::<_, {leaf_rust_type}>(&{key_tuple_str})"
                if leaf_rust_type == "U256":
                    return f"{get_expr}.unwrap_or_else(|| U256::from_u32(&env, 0))"
                elif leaf_rust_type in ("i128", "bool", "u64", "u32", "i64", "i32", "u128"):
                    return f"{get_expr}.unwrap_or_default()"
                else:
                    return f"{get_expr}.unwrap()"
            else:
                value_str = self.transpile_expr(node.value)
                key_str = self.transpile_expr(node.slice)
                # Strip .clone() for .get() receiver
                base = value_str
                if base.endswith(".clone()"):
                    base = base[:-8]
                # Check if base is Option<T>, unwrap first
                need_unwrap = False
                if isinstance(node.value, ast.Name):
                    if node.value.id in self.option_vars:
                        need_unwrap = True
                if need_unwrap:
                    # Use as_ref() to borrow rather than move Option value
                    base = f"{base}.as_ref().unwrap()"
                # Cast index to u32 for Vec types
                is_vec = self._is_vec_typed(node.value)
                if is_vec:
                    return f"{base}.get({key_str} as u32).unwrap()"
                # Wrap key in into_val for Val-typed container
                if self._is_val_typed(node.value):
                    return f"{base}.get({key_str}.into_val(&env)).unwrap()"
                return f"{base}.get({key_str}).unwrap()"

        elif isinstance(node, ast.Call):
            return self._transpile_call(node)

        elif isinstance(node, ast.Compare):
            # Check for 'is None' / 'is not None'
            if len(node.ops) == 1:
                if isinstance(node.ops[0], ast.Is) and isinstance(node.comparators[0], ast.Constant) and node.comparators[0].value is None:
                    left = self.transpile_expr(node.left)
                    if left.endswith('.clone()'):
                        left = left[:-8]
                    return f"{left}.is_none()"
                elif isinstance(node.ops[0], ast.IsNot) and isinstance(node.comparators[0], ast.Constant) and node.comparators[0].value is None:
                    left = self.transpile_expr(node.left)
                    if left.endswith('.clone()'):
                        left = left[:-8]
                    return f"{left}.is_some()"
                elif isinstance(node.ops[0], ast.In):
                    # x in collection -> not directly supported, use contains or check
                    return f"/* TODO: in operator */ true"
                elif isinstance(node.ops[0], ast.NotIn):
                    return f"/* TODO: not in operator */ true"

            any_u256 = self.is_u256_type(node.left) or any(self.is_u256_type(c) for c in node.comparators)
            lhs_is_val = self._is_val_typed(node.left)
            rhs_are_val = [self._is_val_typed(c) for c in node.comparators]
            
            def get_concrete_type(c):
                """Try to infer concrete type from non-Val expression."""
                if isinstance(c, ast.Constant):
                    if isinstance(c.value, bool):
                        return "bool"
                    elif isinstance(c.value, (int, float)):
                        return "u128"
                    elif isinstance(c.value, str):
                        return "Symbol"
                t = self.get_expr_type(c)
                if t and "Val" not in t and t != "Symbol":
                    return t
                if isinstance(c, ast.Call):
                    if isinstance(c.func, ast.Name):
                        name = c.func.id
                        if name == "U128": return "u128"
                        if name == "U64": return "u64"
                        if name == "U32": return "u32"
                if isinstance(c, ast.Name):
                    if c.id in self.local_var_types:
                        return self.local_var_types[c.id]
                if isinstance(c, ast.Attribute):
                    if isinstance(c.value, ast.Name) and c.value.id in self.const_classes:
                        return "u32"
                return None
            
            def transpile_comparator(c, target_concrete_type=None):
                if any_u256 and not self.is_u256_type(c):
                    return self._coerce_to_u256(c)
                expr = self.transpile_expr(c)
                # If this expression is Val-typed and we need to convert to concrete
                if target_concrete_type and self._is_val_typed(c):
                    expr = f"{target_concrete_type}::try_from_val(&env, &{expr}).unwrap()"
                return expr
            
            # Determine which side(s) are Val-typed and need conversion
            val_sides = [lhs_is_val] + rhs_are_val
            any_val_side = any(val_sides)
            
            # If exactly one side is Val, convert that side to the other's concrete type
            # If both are Val, convert both to common type (u64)
            left_tgt = None
            if lhs_is_val:
                for comparator in node.comparators:
                    t = get_concrete_type(comparator)
                    if t and "Val" not in t:
                        left_tgt = t
                        break

            left = transpile_comparator(node.left, left_tgt)
            ops = []
            for idx, (op, comparator) in enumerate(zip(node.ops, node.comparators)):
                rhs_val = rhs_are_val[idx]
                right_tgt = None
                if rhs_val:
                    t = get_concrete_type(node.left)
                    if t and "Val" not in t:
                        right_tgt = t
                right = transpile_comparator(comparator, right_tgt)
                if isinstance(op, ast.NotEq):
                    ops.append(f"{left} != {right}")
                elif isinstance(op, ast.Eq):
                    ops.append(f"{left} == {right}")
                elif isinstance(op, ast.Lt):
                    ops.append(f"{left} < {right}")
                elif isinstance(op, ast.LtE):
                    ops.append(f"{left} <= {right}")
                elif isinstance(op, ast.Gt):
                    ops.append(f"{left} > {right}")
                elif isinstance(op, ast.GtE):
                    ops.append(f"{left} >= {right}")
                else:
                    ops.append(f"{left} == {right}")
                left = right
            return " && ".join(ops)

        elif isinstance(node, ast.BinOp):
            left_is_u256 = self.is_u256_type(node.left)
            right_is_u256 = self.is_u256_type(node.right)

            if left_is_u256 or right_is_u256:
                left = self._coerce_to_u256(node.left) if not left_is_u256 else self.transpile_expr(node.left)
                right = self._coerce_to_u256(node.right) if not right_is_u256 else self.transpile_expr(node.right)
                if isinstance(node.op, ast.Add): return f"{left}.add(&{right})"
                elif isinstance(node.op, ast.Sub): return f"{left}.sub(&{right})"
                elif isinstance(node.op, ast.Mult): return f"{left}.mul(&{right})"
                elif isinstance(node.op, ast.Div): return f"{left}.div(&{right})"
                elif isinstance(node.op, ast.Mod): return f"{left}.rem_euclid(&{right})"

            # Check for string concatenation used outside storage key context
            if isinstance(node.op, ast.Add):
                # If either side is a string constant, treat as key concatenation
                if (isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)) or \
                   (isinstance(node.right, ast.Constant) and isinstance(node.right.value, str)) or \
                   isinstance(node.left, ast.JoinedStr) or isinstance(node.right, ast.JoinedStr):
                    parts = self._extract_key_parts(node)
                    if len(parts) == 1:
                        return parts[0]
                    return f"({', '.join(parts)})"

            left_is_val = self._is_val_typed(node.left)
            right_is_val = self._is_val_typed(node.right)

            left = self.transpile_expr(node.left)
            right = self.transpile_expr(node.right)

            if left_is_val and not right_is_val:
                t = self.get_expr_type(node.right)
                if t and "Val" not in t and t != "Symbol":
                    left = f"{t}::try_from_val(&env, &{left}).unwrap()"
            elif right_is_val and not left_is_val:
                t = self.get_expr_type(node.left)
                if t and "Val" not in t and t != "Symbol":
                    right = f"{t}::try_from_val(&env, &{right}).unwrap()"
            elif left_is_val and right_is_val:
                left = f"u128::try_from_val(&env, &{left}).unwrap()"
                right = f"u128::try_from_val(&env, &{right}).unwrap()"

            op_map = {
                ast.Add: "+", ast.Sub: "-", ast.Mult: "*",
                ast.Div: "/", ast.Mod: "%", ast.BitAnd: "&",
                ast.BitOr: "|", ast.BitXor: "^", ast.LShift: "<<",
                ast.RShift: ">>", ast.FloorDiv: "/", ast.Pow: "/* pow */"
            }
            op_str = op_map.get(type(node.op), "+")
            return f"{left} {op_str} {right}"

        elif isinstance(node, ast.UnaryOp):
            operand_is_val = self._is_val_typed(node.operand)
            operand = self.transpile_expr(node.operand)
            if isinstance(node.op, ast.Not):
                if operand_is_val:
                    operand = f"bool::try_from_val(&env, &{operand}).unwrap()"
                return f"!{operand}"
            elif isinstance(node.op, ast.USub):
                if operand_is_val:
                    operand = f"i128::try_from_val(&env, &{operand}).unwrap()"
                return f"-{operand}"
            return operand

        elif isinstance(node, ast.BoolOp):
            values = [self.transpile_expr(v) for v in node.values]
            op_str = " && " if isinstance(node.op, ast.And) else " || "
            return op_str.join(values)

        elif isinstance(node, ast.IfExp):
            test_str = self.transpile_expr(node.test)
            body_str = self.transpile_expr(node.body)
            else_str = self.transpile_expr(node.orelse)
            return f"if {test_str} {{ {body_str} }} else {{ {else_str} }}"

        return ast.unparse(node)

    def _transpile_call(self, node):
        """Transpile an ast.Call node."""
        # ── 1. self.storage.* calls ──
        storage_method = self._is_self_storage_call(node)
        if storage_method:
            if storage_method == 'get':
                key_str = self._transpile_storage_key(node.args[0])
                key_type = self._get_storage_key_type(node.args[0])
                if len(node.args) >= 2:
                    default_node = node.args[1]
                    if isinstance(default_node, ast.Constant) and default_node.value is None:
                        if key_type:
                            return f"env.storage().instance().get::<_, {key_type}>({key_str})"
                        return f"env.storage().instance().get::<_, soroban_sdk::Val>({key_str})"
                    default_str = self.transpile_expr(default_node)
                    if key_type:
                        return f"env.storage().instance().get::<_, {key_type}>({key_str}).unwrap_or({default_str})"
                    return f"env.storage().instance().get({key_str}).unwrap_or({default_str})"
                else:
                    if key_type:
                        return f"env.storage().instance().get::<_, {key_type}>({key_str}).unwrap()"
                    return f"env.storage().instance().get({key_str}).unwrap()"
            elif storage_method == 'has':
                key_str = self._transpile_storage_key(node.args[0])
                return f"env.storage().instance().has({key_str})"
            elif storage_method == 'set':
                # Handled in transpile_stmt, but can appear as expression
                key_str = self._transpile_storage_key(node.args[0])
                val_str = self.transpile_expr(node.args[1])
                return f"env.storage().instance().set({key_str}, &({val_str}))"
            elif storage_method == 'remove':
                key_str = self._transpile_storage_key(node.args[0])
                return f"env.storage().instance().remove({key_str})"

        # ── 2. self.env.X().Y() chains (ledger, crypto) ──
        chain = self._get_self_env_chain(node)
        if chain:
            x, y = chain
            if x == 'ledger' and y == 'timestamp':
                return "env.ledger().timestamp()"
            elif x == 'ledger' and y == 'sequence':
                return "env.ledger().sequence()"
            elif x == 'ledger' and y == 'ledger_timestamp':
                return "env.ledger().timestamp()"
            elif x == 'ledger' and y == 'ledger_sequence':
                return "env.ledger().sequence()"
            elif x == 'crypto' and y == 'sha256':
                arg = self.transpile_expr(node.args[0])
                return f"env.crypto().sha256(&soroban_sdk::Bytes::from_val(&env, &{arg}.into_val(&env)))"
            elif x == 'crypto' and y == 'keccak256':
                # keccak256 can have multiple args -> hash their concatenation
                if len(node.args) == 1:
                    arg = self.transpile_expr(node.args[0])
                    return f"env.crypto().keccak256(&soroban_sdk::Bytes::from_val(&env, &{arg}.into_val(&env)))"
                else:
                    # Multiple args: concat and hash
                    args_str = ", ".join([f"{self.transpile_expr(a)}.into_val(&env)" for a in node.args])
                    return f"env.crypto().keccak256(&soroban_sdk::Bytes::from_val(&env, &soroban_sdk::vec![&env, {args_str}].into_val(&env)))"
            elif x == 'crypto' and y == 'verify_sig_ed25519':
                pk = self.transpile_expr(node.args[0])
                msg = self.transpile_expr(node.args[1])
                sig = self.transpile_expr(node.args[2])
                return f"env.crypto().ed25519_verify(&{pk}, &{msg}, &{sig})"
            elif x == 'events' and y == 'publish':
                # Direct events().publish() call
                topics = self.transpile_expr(node.args[0])
                data = self.transpile_expr(node.args[1]) if len(node.args) > 1 else "()"
                return f"env.events().publish({topics}, {data})"
            elif x == 'token' and y == 'balance':
                # self.env.token(asset).balance(addr)
                token_arg = self.transpile_expr(node.func.value.args[0])
                balance_arg = self.transpile_expr(node.args[0])
                return f"env.invoke_contract::<soroban_sdk::Val>(&{token_arg}, &Symbol::new(&env, \"balance\"), soroban_sdk::vec![&env, {balance_arg}.into_val(&env)])"

        # ── 3. self.env.* direct calls ──
        env_call = self._is_self_env_call(node)
        if env_call:
            if env_call in ('current_contract_address', 'current_contract'):
                return "env.current_contract_address()"
            elif env_call == 'ledger_timestamp':
                return "env.ledger().timestamp()"
            elif env_call == 'ledger_sequence':
                return "env.ledger().sequence()"
            elif env_call in ('invoke_contract', 'call'):
                contract = self.transpile_expr(node.args[0])
                method_sym_node = node.args[1]
                # Determine return type from method name
                ret_type = "soroban_sdk::Val"
                if isinstance(method_sym_node, ast.Constant) and isinstance(method_sym_node.value, str):
                    method_name = method_sym_node.value
                    if method_name in ('transfer', 'transfer_from', 'approve', 'burn'):
                        ret_type = "bool"
                    elif method_name in ('balance', 'total_supply', 'allowance'):
                        ret_type = "u128"
                    elif method_name in ('decimals',):
                        ret_type = "u32"
                method_sym = self.transpile_expr(method_sym_node)
                if len(node.args) > 2:
                    if len(node.args) == 3 and isinstance(node.args[2], ast.List):
                        arg_parts = [f"{self.transpile_expr(a)}.into_val(&env)" for a in node.args[2].elts]
                        args_vec = f"soroban_sdk::vec![&env, {', '.join(arg_parts)}]"
                    elif len(node.args) == 3 and isinstance(node.args[2], ast.Name) and self._is_val_typed(node.args[2]):
                        args_vec = self.transpile_expr(node.args[2])
                    else:
                        arg_parts = [f"{self.transpile_expr(a)}.into_val(&env)" for a in node.args[2:]]
                        args_vec = f"soroban_sdk::vec![&env, {', '.join(arg_parts)}]"
                else:
                    args_vec = "soroban_sdk::vec![&env]"
                return f"env.invoke_contract::<{ret_type}>(&{contract}, &{method_sym}, {args_vec})"
            elif env_call == 'mint':
                token = self.transpile_expr(node.args[0])
                to = self.transpile_expr(node.args[1])
                amount = self.transpile_expr(node.args[2])
                return f"env.invoke_contract::<soroban_sdk::Val>(&{token}, &Symbol::new(&env, \"mint\"), soroban_sdk::vec![&env, {to}.into_val(&env), {amount}.into_val(&env)])"
            elif env_call == 'burn':
                token = self.transpile_expr(node.args[0])
                from_addr = self.transpile_expr(node.args[1])
                amount = self.transpile_expr(node.args[2])
                return f"env.invoke_contract::<soroban_sdk::Val>(&{token}, &Symbol::new(&env, \"burn\"), soroban_sdk::vec![&env, {from_addr}.into_val(&env), {amount}.into_val(&env)])"
            elif env_call == 'transfer':
                from_addr = self.transpile_expr(node.args[0])
                to_addr = self.transpile_expr(node.args[1])
                token = self.transpile_expr(node.args[2])
                amount = self.transpile_expr(node.args[3])
                return f"env.invoke_contract::<soroban_sdk::Val>(&{token}, &Symbol::new(&env, \"transfer\"), soroban_sdk::vec![&env, {from_addr}.into_val(&env), {to_addr}.into_val(&env), {amount}.into_val(&env)])"
            elif env_call == 'emit_event':
                topic = self.transpile_expr(node.args[0])
                if len(node.args) >= 2:
                    data_node = node.args[1]
                    if isinstance(data_node, ast.Dict):
                        data_vals = [self.transpile_expr(v) for v in data_node.values]
                        data_str = f"({', '.join(data_vals)},)" if len(data_vals) == 1 else f"({', '.join(data_vals)})"
                    else:
                        data_str = self.transpile_expr(data_node)
                else:
                    data_str = "()"
                return f"env.events().publish(({topic},), {data_str})"
            elif env_call == 'serialize':
                arg = self.transpile_expr(node.args[0])
                if arg.endswith('.clone()'):
                    arg = arg[:-8]
                return f"{arg}.clone().to_xdr(&env)"
            elif env_call == 'deployer':
                return "env.deployer()"
            elif env_call == 'storage':
                return "env.storage().instance()"

        # ── 4. Built-in functions ──
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            if func_name == 'Symbol':
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    return f'Symbol::new(&env, "{node.args[0].value}")'
                elif node.args:
                    arg_str = self.transpile_expr(node.args[0])
                    return f'Symbol::new(&env, &{arg_str})'
            elif func_name == 'Map':
                return 'soroban_sdk::Map::<soroban_sdk::Val, soroban_sdk::Val>::new(&env)'
            elif func_name == 'Vec':
                return 'soroban_sdk::Vec::<soroban_sdk::Val>::new(&env)'
            elif func_name == 'Bytes' and not node.args:
                return 'soroban_sdk::Bytes::new(&env)'
            elif func_name == 'Bytes' and len(node.args) == 1:
                arg_str = self.transpile_expr(node.args[0])
                return f'soroban_sdk::Bytes::from_slice(&env, {arg_str})'
            elif func_name == 'Address' and len(node.args) == 1:
                if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    return f'soroban_sdk::Address::from_string(&soroban_sdk::String::from_str(&env, "{node.args[0].value}"))'
                else:
                    arg_str = self.transpile_expr(node.args[0])
                    return f'soroban_sdk::Address::from_string(&soroban_sdk::String::from_str(&env, &{arg_str}))'
            elif func_name == 'len':
                arg = self.transpile_expr(node.args[0])
                if arg.endswith('.clone()'):
                    arg = arg[:-8]
                return f"{arg}.len()"
            elif func_name == 'range':
                if len(node.args) == 1:
                    limit = self.transpile_expr(node.args[0])
                    return f"0..{limit}"
                elif len(node.args) >= 2:
                    start = self.transpile_expr(node.args[0])
                    end = self.transpile_expr(node.args[1])
                    return f"{start}..{end}"
            elif func_name == 'int':
                if node.args:
                    return self.transpile_expr(node.args[0])
                return "0"
            elif func_name == 'str':
                if node.args:
                    return self.transpile_expr(node.args[0])
                return 'Symbol::new(&env, "")'
            elif func_name == 'abs':
                if node.args:
                    arg = self.transpile_expr(node.args[0])
                    return f"({arg}).abs()"
                return "0"
            elif func_name == 'U128':
                if node.args:
                    inner = node.args[0]
                    if isinstance(inner, ast.Constant) and isinstance(inner.value, (int, float)):
                        return f"{inner.value}_u128"
                    return f"(({self.transpile_expr(inner)}) as u128)"
                return "0_u128"
            elif func_name == 'U64':
                if node.args:
                    inner = node.args[0]
                    if isinstance(inner, ast.Constant) and isinstance(inner.value, (int, float)):
                        return f"{inner.value}_u64"
                    return f"(({self.transpile_expr(inner)}) as u64)"
                return "0_u64"
            elif func_name == 'U32':
                if node.args:
                    inner = node.args[0]
                    if isinstance(inner, ast.Constant) and isinstance(inner.value, (int, float)):
                        return f"{inner.value}_u32"
                    return f"(({self.transpile_expr(inner)}) as u32)"
                return "0_u32"
            elif func_name == 'I128':
                if node.args:
                    inner = node.args[0]
                    if isinstance(inner, ast.Constant) and isinstance(inner.value, (int, float)):
                        return f"{inner.value}_i128"
                    return f"(({self.transpile_expr(inner)}) as i128)"
                return "0_i128"
            elif func_name == 'I32':
                if node.args:
                    inner = node.args[0]
                    if isinstance(inner, ast.Constant) and isinstance(inner.value, (int, float)):
                        return f"{inner.value}_i32"
                    return f"(({self.transpile_expr(inner)}) as i32)"
                return "0_i32"
            elif func_name == 'Bool':
                if node.args:
                    return self.transpile_expr(node.args[0])
                return "false"
            elif func_name == 'print':
                # Ignore print statements
                return "/* print */"

        # ── 5. Method calls on objects ──
        if isinstance(node.func, ast.Attribute):
            method = node.func.attr

            # .append() -> .push_back()
            if method == 'append':
                if isinstance(node.func.value, ast.Name):
                    base_str = escape_keyword(node.func.value.id)
                    var_name = node.func.value.id
                    arg_type = self.get_expr_type(node.args[0])
                    if arg_type and arg_type != "Symbol" and "Val" not in arg_type:
                        self.local_var_types[var_name] = f"soroban_sdk::Vec<{arg_type}>"
                else:
                    base_str = self.transpile_expr(node.func.value)
                if base_str.endswith(".clone()"):
                    base_str = base_str[:-8]
                arg_str = self.transpile_expr(node.args[0])
                if self._is_val_typed(node.func.value):
                    return f"{base_str}.push_back({arg_str}.into_val(&env))"
                return f"{base_str}.push_back({arg_str})"

            # .require_auth()
            if method == 'require_auth':
                base = self.transpile_expr(node.func.value)
                if base.endswith('.clone()'):
                    base = base[:-8]
                return f"{base}.require_auth()"

            # .length() -> .len()
            if method == 'length':
                base = self.transpile_expr(node.func.value)
                if base.endswith('.clone()'):
                    base = base[:-8]
                return f"{base}.len()"

            # .len()
            if method == 'len' and not node.args:
                base = self.transpile_expr(node.func.value)
                if base.endswith('.clone()'):
                    base = base[:-8]
                return f"{base}.len()"

            # .keys()
            if method == 'keys' and not node.args:
                base = self.transpile_expr(node.func.value)
                if base.endswith('.clone()'):
                    base = base[:-8]
                return f"{base}.keys()"

            # .get(index) on Vec/Map
            if method == 'get' and len(node.args) == 1:
                # Skip if this is a storage or env chain (already handled above)
                if not (isinstance(node.func.value, ast.Attribute) and node.func.value.attr in ('storage', 'env')):
                    base = self.transpile_expr(node.func.value)
                    if base.endswith('.clone()'):
                        base = base[:-8]
                    key = self.transpile_expr(node.args[0])
                    if self._is_vec_typed(node.func.value):
                        return f"{base}.get({key} as u32).unwrap()"
                    if self._is_val_typed(node.func.value):
                        return f"{base}.get({key}.into_val(&env)).unwrap()"
                    if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, int):
                        return f"{base}.get({key} as u32).unwrap()"
                    return f"{base}.get({key}).unwrap()"

            # .set(key, value) on Map/Vec
            if method == 'set' and len(node.args) == 2:
                if not (isinstance(node.func.value, ast.Attribute) and node.func.value.attr in ('storage',)):
                    base = self.transpile_expr(node.func.value)
                    if base.endswith('.clone()'):
                        base = base[:-8]
                    key = self.transpile_expr(node.args[0])
                    val = self.transpile_expr(node.args[1])
                    if self._is_vec_typed(node.func.value):
                        base_type = self.get_expr_type(node.func.value)
                        if base_type and "Val" in base_type:
                            return f"{base}.set({key} as u32, {val}.into_val(&env))"
                        return f"{base}.set({key} as u32, {val})"
                    if self._is_val_typed(node.func.value):
                        return f"{base}.set({key}.into_val(&env), {val}.into_val(&env))"
                    return f"{base}.set({key}, {val})"

            # .has(key) on Map
            if method == 'has' and len(node.args) == 1:
                if not (isinstance(node.func.value, ast.Attribute) and node.func.value.attr in ('storage',)):
                    base = self.transpile_expr(node.func.value)
                    if base.endswith('.clone()'):
                        base = base[:-8]
                    key = self.transpile_expr(node.args[0])
                    return f"{base}.has({key})"

            # .remove(index/key)
            if method == 'remove' and len(node.args) == 1:
                if not (isinstance(node.func.value, ast.Attribute) and node.func.value.attr in ('storage',)):
                    base = self.transpile_expr(node.func.value)
                    if base.endswith('.clone()'):
                        base = base[:-8]
                    arg = self.transpile_expr(node.args[0])
                    if self._is_vec_typed(node.func.value):
                        return f"{base}.remove({arg} as u32)"
                    return f"{base}.remove({arg})"

            # .push_back()
            if method == 'push_back':
                base = self.transpile_expr(node.func.value)
                if base.endswith('.clone()'):
                    base = base[:-8]
                arg = self.transpile_expr(node.args[0])
                if self._is_val_typed(node.func.value):
                    return f"{base}.push_back({arg}.into_val(&env))"
                return f"{base}.push_back({arg})"

            # .to_bytes()
            if method == 'to_bytes' and not node.args:
                base = self.transpile_expr(node.func.value)
                return f"soroban_sdk::Bytes::from_val(&env, &{base}.into_val(&env))"

            # .to_string()
            if method == 'to_string' and not node.args:
                base = self.transpile_expr(node.func.value)
                return f"soroban_sdk::String::from_val(&env, &{base}.into_val(&env))"

            # .concat()
            if method == 'concat' and len(node.args) == 1:
                base = self.transpile_expr(node.func.value)
                arg = self.transpile_expr(node.args[0])
                return f"{base}.append(&{arg})"

            # .from_string() on Address
            if method == 'from_string':
                if isinstance(node.func.value, ast.Name) and node.func.value.id == 'Address':
                    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        return f'soroban_sdk::Address::from_string(&soroban_sdk::String::from_str(&env, "{node.args[0].value}"))'
                    else:
                        arg_str = self.transpile_expr(node.args[0])
                        return f'soroban_sdk::Address::from_string(&soroban_sdk::String::from_str(&env, &{arg_str}))'

            # .balance(), .total_supply() etc on token – treat as invoke_contract
            if method == 'latest_round_data':
                base = self.transpile_expr(node.func.value)
                return f"env.invoke_contract::<soroban_sdk::Val>(&{base}, &Symbol::new(&env, \"latest_round_data\"), soroban_sdk::vec![&env])"

            # .deploy() on deployer
            if method == 'deploy':
                base = self.transpile_expr(node.func.value)
                if node.args:
                    args_str = ", ".join([self.transpile_expr(a) for a in node.args])
                    return f"{base}.deploy({args_str})"
                return f"{base}.deploy()"

            # .upload_contract_wasm()
            if method == 'upload_contract_wasm':
                base = self.transpile_expr(node.func.value)
                arg = self.transpile_expr(node.args[0])
                return f"{base}.upload_contract_wasm({arg})"

            # .with_current_contract()
            if method == 'with_current_contract':
                base = self.transpile_expr(node.func.value)
                arg = self.transpile_expr(node.args[0])
                return f"{base}.with_current_contract({arg})"

        # ── 6. Event detection (old style) ──
        is_event = False
        func_name = None
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            if func_name in self.events or (func_name[0:1].isupper() and func_name not in ("Bytes", "Map", "Vec", "Symbol", "Address", "U256", "U128", "U64", "U32", "I128", "I32", "Bool")):
                is_event = True
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == 'self':
            func_name = node.func.attr

        if is_event and func_name and func_name in self.events:
            event_meta = self.events.get(func_name, {"fields": {}})
            fields = list(event_meta["fields"].keys())
            topics = []
            data = []
            for i, arg in enumerate(node.args):
                arg_str = self.transpile_expr(arg)
                field_name = fields[i] if i < len(fields) else ""
                field_type = event_meta["fields"].get(field_name, "")
                if "indexed(" in field_type:
                    topics.append(arg_str)
                else:
                    data.append(arg_str)
            topics_list = [f'Symbol::new(&env, "{func_name}")'] + topics
            topics_str = f"({', '.join(topics_list)},)" if len(topics_list) == 1 else f"({', '.join(topics_list)})"
            if len(data) == 0: data_str = "()"
            elif len(data) == 1: data_str = data[0]
            else: data_str = f"({', '.join(data)})"
            return f"env.events().publish({topics_str}, &{data_str})"

        # ── 7. self.method() calls ──
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == 'self':
            method_name = node.func.attr
            args_list = ["env.clone()"] + [self.transpile_expr(a) for a in node.args] + [self.transpile_expr(k.value) for k in node.keywords]
            return f"Self::{method_name}({', '.join(args_list)})"

        # ── 8. Fallback: generic function call ──
        func_str = self.transpile_expr(node.func)
        args_list = [self.transpile_expr(a) for a in node.args] + [self.transpile_expr(k.value) for k in node.keywords]
        args_str = ", ".join(args_list)
        return f"{func_str}({args_str})"

    # ── Expression type inference ──────────────────────────────────────

    def get_expr_type(self, node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return "bool"
            elif isinstance(node.value, (int, float)):
                return None
            elif isinstance(node.value, str):
                return "Symbol"
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                name = node.func.id
                if name == "Map": return "soroban_sdk::Map<soroban_sdk::Val, soroban_sdk::Val>"
                elif name == "Vec": return "soroban_sdk::Vec<soroban_sdk::Val>"
                elif name == "Bytes": return "soroban_sdk::Bytes"
                elif name == "U128": return "u128"
                elif name == "U64": return "u64"
                elif name == "U32": return "u32"
                elif name == "I128": return "i128"
                elif name == "I32": return "i32"
                elif name == "Bool": return "bool"
                elif name == "Symbol": return "Symbol"
                elif name == "Address": return "Address"
                elif name == "len": return "u32"
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in ('len', 'length'):
                    return "u32"
                if node.func.attr == 'get':
                    base_type = self.get_expr_type(node.func.value)
                    if base_type:
                        if base_type.startswith("soroban_sdk::Vec<") and base_type.endswith(">"):
                            return base_type[17:-1]
                        elif base_type.startswith("soroban_sdk::Map<") and base_type.endswith(">"):
                            parts = base_type[17:-1].split(",")
                            if len(parts) == 2:
                                return parts[1].strip()
            # self.storage.get(key, default) -> infer from default
            storage_method = self._is_self_storage_call(node)
            if storage_method == 'get':
                key_type = self._get_storage_key_type(node.args[0])
                if key_type:
                    return key_type
                if len(node.args) >= 2:
                    return self.get_expr_type(node.args[1])
                return "Symbol"
            if storage_method == 'has':
                return "bool"
            if storage_method == 'set':
                return "()"
            # Detect env chain return types
            chain = self._get_self_env_chain(node)
            if chain:
                x, y = chain
                if x == 'ledger' and y == 'timestamp':
                    return "u64"
                elif x == 'ledger' and y == 'sequence':
                    return "u32"
                elif x == 'crypto' and y in ('sha256', 'keccak256'):
                    return "soroban_sdk::Bytes"
                elif x == 'crypto' and y == 'verify_sig_ed25519':
                    return "bool"
                elif x == 'token' and y == 'balance':
                    return "u128"
            env_call = self._is_self_env_call(node)
            if env_call:
                if env_call == 'ledger_timestamp':
                    return "u64"
                elif env_call == 'ledger_sequence':
                    return "u32"
                elif env_call == 'current_contract_address':
                    return "Address"
                elif env_call == 'current_contract':
                    return "Address"
                elif env_call in ('invoke_contract', 'call'):
                    if len(node.args) >= 2:
                        method_node = node.args[1]
                        if isinstance(method_node, ast.Constant) and isinstance(method_node.value, str):
                            method_name = method_node.value
                            if method_name in ('transfer', 'transfer_from', 'approve', 'burn'):
                                return "bool"
                            elif method_name in ('balance', 'total_supply', 'allowance'):
                                return "u128"
                    return "soroban_sdk::Val"
                elif env_call == 'transfer':
                    return "bool"
                elif env_call == 'mint':
                    return "soroban_sdk::Val"
                elif env_call == 'burn':
                    return "bool"
                elif env_call == 'serialize':
                    return "soroban_sdk::Bytes"
        elif isinstance(node, ast.Name):
            return self.local_var_types.get(node.id, "Symbol")
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == 'self':
                var_info = self.state_variables.get(node.attr, {})
                return map_type(var_info.get("type", "Symbol"))
        elif isinstance(node, ast.Subscript):
            attr, keys = flatten_subscript(node)
            if attr and attr in self.state_variables:
                var_info = self.state_variables[attr]
                return map_type(get_subscript_type(var_info.get("type", ""), len(keys)))
            elif attr and attr in self.local_var_types:
                var_type = self.local_var_types[attr]
                if var_type.startswith("soroban_sdk::Vec<") and var_type.endswith(">"):
                    return var_type[17:-1]
                elif var_type.startswith("soroban_sdk::Map<") and var_type.endswith(">"):
                    parts = var_type[17:-1].split(",")
                    if len(parts) == 2:
                        return parts[1].strip()
        elif isinstance(node, ast.Dict):
            return "soroban_sdk::Map<soroban_sdk::Val, soroban_sdk::Val>"
        elif isinstance(node, ast.List):
            return "soroban_sdk::Vec<soroban_sdk::Val>"
        elif isinstance(node, ast.BinOp):
            left = self.get_expr_type(node.left)
            if left != "Symbol":
                return left
            right = self.get_expr_type(node.right)
            if right != "Symbol":
                return right
            return "u128"
        elif isinstance(node, ast.UnaryOp):
            return self.get_expr_type(node.operand)
        elif isinstance(node, ast.BoolOp):
            return "bool"
        elif isinstance(node, ast.Compare):
            return "bool"
        elif isinstance(node, ast.IfExp):
            return self.get_expr_type(node.body)
        return "Symbol"

    def _transpile_assignment(self, target, val_str, val_node=None):
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'self':
            var_name = target.attr
            if var_name in ('env', 'storage'):
                return f"// skip self.{var_name} assignment"
            return f'env.storage().instance().set(&Symbol::new(&env, "{var_name}"), &({val_str}));'
        elif isinstance(target, ast.Subscript):
            attr, keys = flatten_subscript(target)
            if attr and attr in self.state_variables:
                transpiled_keys = []
                for k in keys:
                    k_str = self.transpile_expr(k)
                    transpiled_keys.append(k_str if k_str.endswith(".clone()") else f"{k_str}.clone()")
                key_tuple_elements = [f'Symbol::new(&env, "{attr}")'] + transpiled_keys
                key_tuple_str = f"({', '.join(key_tuple_elements)},)" if len(key_tuple_elements) == 1 else f"({', '.join(key_tuple_elements)})"
                return f"env.storage().instance().set(&{key_tuple_str}, &({val_str}));"
            else:
                if isinstance(target.value, ast.Name):
                    base_str = escape_keyword(target.value.id)
                else:
                    base_str = self.transpile_expr(target.value)
                if base_str.endswith('.clone()'):
                    base_str = base_str[:-8]
                if isinstance(target.value, ast.Name) and target.value.id in self.option_vars:
                    base_str = f"{base_str}.as_mut().unwrap()"
                key_str = self.transpile_expr(target.slice)
                
                is_vec = self._is_vec_typed(target.value)
                
                if self._is_val_typed(target.value):
                    if is_vec:
                        return f"{base_str}.set({key_str} as u32, {val_str}.into_val(&env));"
                    return f"{base_str}.set({key_str}.into_val(&env), {val_str}.into_val(&env));"
                
                if is_vec:
                    return f"{base_str}.set({key_str} as u32, {val_str});"
                return f"{base_str}.set({key_str}, {val_str});"
        elif isinstance(target, ast.Name):
            var_name = escape_keyword(target.id)
            if val_node:
                expr_type = self.get_expr_type(val_node)
                self.local_var_types[target.id] = expr_type
                self.local_var_exprs[target.id] = val_node
                if (isinstance(val_node, ast.Call) and
                    isinstance(val_node.func, ast.Attribute) and
                    val_node.func.attr == 'get' and
                    isinstance(val_node.func.value, ast.Attribute) and
                    val_node.func.value.attr == 'storage' and
                    isinstance(val_node.func.value.value, ast.Name) and
                    val_node.func.value.value.id == 'self' and
                    len(val_node.args) >= 2):
                    default_node = val_node.args[1]
                    if isinstance(default_node, ast.Constant) and default_node.value is None:
                        key_type = self._get_storage_key_type(val_node.args[0])
                        inner_type = key_type or "soroban_sdk::Val"
                        self.option_vars[target.id] = inner_type
                        self.local_var_types[target.id] = f"Option<{inner_type}>"
                if expr_type == 'U256' and isinstance(val_node, ast.Constant) and isinstance(val_node.value, (int, float)):
                    val_str = f'U256::from_u32(&env, {val_node.value})'
            
            if target.id not in self.local_vars:
                self.local_vars.add(target.id)
                return f"let mut {var_name} = {val_str};"
            else:
                return f"{var_name} = {val_str};"
        return f"{self.transpile_expr(target)} = {val_str};"

    # ── Statement transpilation ────────────────────────────────────────

    def transpile_stmt(self, node):
        # ── Raise statement ──
        if isinstance(node, ast.Raise):
            if node.exc:
                if isinstance(node.exc, ast.Attribute) and isinstance(node.exc.value, ast.Name) and node.exc.value.id == 'ContractError':
                    error_name = to_pascal_case(node.exc.attr)
                    return f'panic_with_error!(&env, ContractError::{error_name});'
                # raise Exception("msg") or raise SomeError
                return f'panic!("{ast.unparse(node.exc)}");'
            return 'panic!();'

        # ── While loop ──
        if isinstance(node, ast.While):
            test_str = self.transpile_expr(node.test)
            body_stmts = [self.transpile_stmt(s) for s in node.body]
            body_str = "\n        ".join(body_stmts)
            return f"while {test_str} {{\n        {body_str}\n    }}"

        # ── Break / Continue ──
        if isinstance(node, ast.Break):
            return "break;"
        if isinstance(node, ast.Continue):
            return "continue;"

        # ── Assignment ──
        if isinstance(node, ast.Assign):
            target = node.targets[0]

            # Tuple unpacking: a, b = b, a
            if isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple):
                assigns = []
                temp_names = []
                for i, elt in enumerate(node.value.elts):
                    temp_name = f"__tmp_{i}"
                    temp_names.append(temp_name)
                    val_str = self.transpile_expr(elt)
                    assigns.append(f"let {temp_name} = {val_str};")
                for i, tgt in enumerate(target.elts):
                    assigns.append(self._transpile_assignment(tgt, temp_names[i]))
                return "\n        ".join(assigns)

            val_str = self.transpile_expr(node.value)
            return self._transpile_assignment(target, val_str, node.value)

        # ── Annotated assignment ──
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            annotated_type = map_type(ast.unparse(node.annotation))
            if node.value and annotated_type == 'U256' and isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)):
                val_str = f'U256::from_u32(&env, {node.value.value})'
            else:
                val_str = self.transpile_expr(node.value) if node.value else "Default::default()"

            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'self':
                var_name = target.attr
                return f'env.storage().instance().set(&Symbol::new(&env, "{var_name}"), &({val_str}));'
            elif isinstance(target, ast.Name):
                var_name = escape_keyword(target.id)
                self.local_var_types[target.id] = annotated_type
                if target.id not in self.local_vars:
                    self.local_vars.add(target.id)
                    return f"let mut {var_name}: {annotated_type} = {val_str};"
                else:
                    return f"{var_name} = {val_str};"

        # ── Augmented assignment ──
        elif isinstance(node, ast.AugAssign):
            target = node.target
            synthetic_binop = ast.BinOp(left=target, op=node.op, right=node.value)
            val_str = self.transpile_expr(synthetic_binop)

            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'self':
                var_name = target.attr
                return f'env.storage().instance().set(&Symbol::new(&env, "{var_name}"), &({val_str}));'
            elif isinstance(target, ast.Subscript):
                attr, keys = flatten_subscript(target)
                if attr and attr in self.state_variables:
                    transpiled_keys = []
                    for k in keys:
                        k_str = self.transpile_expr(k)
                        transpiled_keys.append(k_str if k_str.endswith(".clone()") else f"{k_str}.clone()")
                    key_tuple_elements = [f'Symbol::new(&env, "{attr}")'] + transpiled_keys
                    key_tuple_str = f"({', '.join(key_tuple_elements)},)" if len(key_tuple_elements) == 1 else f"({', '.join(key_tuple_elements)})"
                    return f"env.storage().instance().set(&{key_tuple_str}, &({val_str}));"
                else:
                    if isinstance(target.value, ast.Name):
                        base_str = escape_keyword(target.value.id)
                    else:
                        base_str = self.transpile_expr(target.value)
                    if base_str.endswith('.clone()'):
                        base_str = base_str[:-8]
                    # Unwrap Option before subscript set (as_mut to borrow, not consume)
                    if isinstance(target.value, ast.Name) and target.value.id in self.option_vars:
                        base_str = f"{base_str}.as_mut().unwrap()"
                    key_str = self.transpile_expr(target.slice)
                    if self._is_val_typed(target.value):
                        return f"{base_str}.set({key_str}.into_val(&env), {val_str}.into_val(&env));"
                    return f"{base_str}.set({key_str}, {val_str});"
            elif isinstance(target, ast.Name):
                var_name = escape_keyword(target.id)
                return f"{var_name} = {val_str};"

        # ── Return ──
        elif isinstance(node, ast.Return):
            if node.value:
                if self.return_type == 'U256' and isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)):
                    return f"return U256::from_u32(&env, {node.value.value});"
                val = self.transpile_expr(node.value)
                # If returning an Option-typed variable, unwrap it
                if isinstance(node.value, ast.Name) and node.value.id in self.option_vars:
                    val = val.rstrip(';')
                    if val.endswith('.clone()'):
                        val = val[:-8]
                    return f"return {val}.unwrap();"
                return f"return {val};"
            return "return;"

        # ── If statement ──
        elif isinstance(node, ast.If):
            test_str = self.transpile_expr(node.test)
            body_stmts = [self.transpile_stmt(s) for s in node.body]
            body_str = "\n        ".join(body_stmts)
            orelse_str = ""
            if node.orelse:
                if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                    # elif
                    orelse_str = " else " + self.transpile_stmt(node.orelse[0])
                else:
                    orelse_stmts = [self.transpile_stmt(s) for s in node.orelse]
                    orelse_str = " else {\n        " + "\n        ".join(orelse_stmts) + "\n    }"
            return f"if {test_str} {{\n        {body_str}\n    }}{orelse_str}"

        # ── For loop ──
        elif isinstance(node, ast.For):
            target_str = self.transpile_expr(node.target)
            if target_str.endswith(".clone()"):
                target_str = target_str[:-8]
            iter_str = self.transpile_expr(node.iter)
            if iter_str.endswith(".clone()"):
                iter_str = iter_str[:-8]
            # Track loop variable type
            if isinstance(node.target, ast.Name):
                self.local_vars.add(node.target.id)
                # If iterating over range or length, it's u32
                if isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Name) and node.iter.func.id in ('range', 'len'):
                    self.local_var_types[node.target.id] = "u32"
                elif isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Attribute) and node.iter.func.attr in ('len', 'length'):
                    self.local_var_types[node.target.id] = "u32"
                else:
                    collection_type = self.get_expr_type(node.iter)
                    if collection_type:
                        if collection_type.startswith("soroban_sdk::Vec<") and collection_type.endswith(">"):
                            self.local_var_types[node.target.id] = collection_type[17:-1]
                        elif collection_type.startswith("soroban_sdk::Map<") and collection_type.endswith(">"):
                            parts = collection_type[17:-1].split(",")
                            self.local_var_types[node.target.id] = parts[0].strip()
                        else:
                            self.local_var_types[node.target.id] = "soroban_sdk::Val"
                    else:
                        self.local_var_types[node.target.id] = "soroban_sdk::Val"
            body_stmts = [self.transpile_stmt(s) for s in node.body]
            body_str = "\n        ".join(body_stmts)
            return f"for {target_str} in {iter_str} {{\n        {body_str}\n    }}"

        # ── Assert ──
        elif isinstance(node, ast.Assert):
            test_node = node.test
            msg_val = None
            if isinstance(test_node, ast.Tuple) and len(test_node.elts) == 2:
                msg_node = test_node.elts[1]
                test_node = test_node.elts[0]
                if isinstance(msg_node, ast.Constant):
                    msg_val = msg_node.value
            test_str = self.transpile_expr(test_node)
            if msg_val is not None:
                msg_str = f', "{msg_val}"'
            elif node.msg and isinstance(node.msg, ast.Constant):
                msg_str = f', "{node.msg.value}"'
            else:
                msg_str = ''
            return f"assert!({test_str}{msg_str});"

        # ── Pass ──
        elif isinstance(node, ast.Pass):
            return "// no-op"

        # ── Expression statement ──
        elif isinstance(node, ast.Expr):
            # Check for docstrings
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                doc = node.value.value.strip()
                # Comment out multi-line docstrings properly
                doc_lines = doc.split('\n')
                if len(doc_lines) <= 1:
                    return f'// {doc[:80]}'
                return '\n        '.join(f'// {line.strip()[:80]}' for line in doc_lines if line.strip())
            expr_str = self.transpile_expr(node.value)
            if not expr_str.endswith(";"):
                expr_str += ";"
            return expr_str

        return ast.unparse(node)


def collect_local_vars(func_node):
    local_vars = set()
    if not func_node:
        return local_vars
    for node in ast.walk(func_node):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    local_vars.add(target.id)
                elif isinstance(target, (ast.Tuple, ast.List)):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            local_vars.add(elt.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                local_vars.add(node.target.id)
        elif isinstance(node, ast.For):
            if isinstance(node.target, ast.Name):
                local_vars.add(node.target.id)
            elif isinstance(node.target, (ast.Tuple, ast.List)):
                for elt in node.target.elts:
                    if isinstance(elt, ast.Name):
                        local_vars.add(elt.id)
    return local_vars


# ─── Code Generation ──────────────────────────────────────────────────────

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
            module_constants=visitor.module_constants
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

    version = "22.0.1"

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
soroban-sdk = "22.0.0"

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
