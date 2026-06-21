import ast
from .utils import (
    escape_keyword,
    to_pascal_case,
    eval_static_constant,
    map_type,
    get_subscript_type,
    flatten_subscript,
)

def to_turbofish(type_str):
    if not type_str:
        return type_str
    if "<" in type_str:
        idx = type_str.find("<")
        if idx >= 2 and type_str[idx-2:idx] != "::":
            return type_str[:idx] + "::" + type_str[idx:]
    return type_str


class NoneComparisonCollector(ast.NodeVisitor):
    def __init__(self):
        self.option_vars = set()
        self.storage_get_vars = set()
        
    @staticmethod
    def _is_option_get(val):
        """True if a `self.storage.get(...)` call yields an Option<T>.

        A get with a concrete default (`get(key, False)`) is lowered to
        `.unwrap_or(<default>)` and yields a concrete value, NOT an Option — so it
        must not turn the target into an Option var. Only a get with no default,
        or an explicit `None` default, yields an Option. Mirrors
        RustTranspiler._storage_get_is_option.
        """
        if not (isinstance(val, ast.Call) and isinstance(val.func, ast.Attribute)):
            return False
        if val.func.attr != 'get':
            return False
        if not (isinstance(val.func.value, ast.Attribute) and val.func.value.attr == 'storage'):
            return False
        if len(val.args) <= 1:
            return True
        default_node = val.args[1]
        return isinstance(default_node, ast.Constant) and default_node.value is None

    def visit_Assign(self, node):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target_id = node.targets[0].id
            if self._is_option_get(node.value):
                self.storage_get_vars.add(target_id)
        self.generic_visit(node)
        
    def visit_Compare(self, node):
        if len(node.ops) == 1:
            op = node.ops[0]
            if isinstance(op, (ast.Is, ast.IsNot, ast.Eq, ast.NotEq)):
                left_none = isinstance(node.left, ast.Constant) and node.left.value is None
                right_none = len(node.comparators) == 1 and isinstance(node.comparators[0], ast.Constant) and node.comparators[0].value is None
                if left_none and not right_none:
                    comp = node.comparators[0]
                    if isinstance(comp, ast.Name):
                        self.option_vars.add(comp.id)
                elif right_none and not left_none:
                    if isinstance(node.left, ast.Name):
                        self.option_vars.add(node.left.id)
        self.generic_visit(node)

    def visit_UnaryOp(self, node):
        if isinstance(node.op, ast.Not) and isinstance(node.operand, ast.Name):
            if node.operand.id in self.storage_get_vars:
                self.option_vars.add(node.operand.id)
        self.generic_visit(node)

    def visit_If(self, node):
        if isinstance(node.test, ast.Name):
            if node.test.id in self.storage_get_vars:
                self.option_vars.add(node.test.id)
        self.generic_visit(node)

    def visit_While(self, node):
        if isinstance(node.test, ast.Name):
            if node.test.id in self.storage_get_vars:
                self.option_vars.add(node.test.id)
        self.generic_visit(node)

class RustTranspiler(ast.NodeVisitor):
    def __init__(self, state_variables, contract_name, events, local_var_types=None,
                 return_type=None, functions_meta=None, has_errors=False,
                 storage_key_types=None, const_classes=None, module_constants=None,
                 func_node=None):
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
        if func_node:
            collector = NoneComparisonCollector()
            collector.visit(func_node)
            for var_name in collector.option_vars:
                current_type = self.local_var_types.get(var_name, "soroban_sdk::Val")
                if not current_type.startswith("Option<"):
                    self.local_var_types[var_name] = f"Option<{current_type}>"
                    self.option_vars[var_name] = current_type
                else:
                    inner = current_type[7:-1]
                    self.option_vars[var_name] = inner

    def _get_base_str(self, base_node):
        base = self.transpile_expr(base_node)
        if base.endswith('.clone()'):
            base = base[:-8]
        if isinstance(base_node, ast.Name) and base_node.id in self.option_vars:
            base = f"{base}.as_ref().unwrap()"
        return base

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
            var_type = self.local_var_types.get(node.id) or ""
            if "soroban_sdk::Val" in var_type:
                return True
        return False

    def _is_vec_typed(self, node):
        """Check if an expression node has type Vec."""
        if isinstance(node, ast.Name):
            var_type = self.local_var_types.get(node.id) or ""
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

    _INT_TYPES = ('u32', 'u64', 'u128', 'i32', 'i64', 'i128')

    def _typed_literal(self, node, target_type):
        """Transpile a constant `node` so its literal type matches `target_type`.

        A bare integer literal otherwise defaults to `u64` and mismatches a u32/u128
        slot (e.g. as a storage `get` default). Numeric wrapper calls (`U64(0)`,
        `U32(0)`, ...) are unwrapped too, since the storage slot's element type — not
        the wrapper — is the authority for the get's return type. For non-constant or
        non-int targets this falls back to the normal expression transpile.
        """
        inner = node
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ('U64', 'U32', 'U128', 'I128', 'I32', 'I64')
                and node.args):
            inner = node.args[0]
        if isinstance(inner, ast.Constant) and isinstance(inner.value, int) and not isinstance(inner.value, bool):
            if target_type in self._INT_TYPES:
                return f"{inner.value}_{target_type}"
        return self.transpile_expr(node)

    def _storage_get_is_option(self, node):
        """Return True if a `self.storage.get(...)` call yields an Option<T> rather
        than a concrete value.

        A storage get is an Option when:
          - it has no default argument: `get(key)`        -> Option<T>
          - its default is literally None: `get(key, None)` -> Option<T>
        It is a concrete value when a non-None default is supplied:
          - `get(key, False)` / `get(key, 0)` -> `.unwrap_or(<default>)` (T, not Option)
        """
        if not (isinstance(node, ast.Call) and self._is_self_storage_call(node) == 'get'):
            return False
        if len(node.args) == 1:
            return True
        default_node = node.args[1]
        return isinstance(default_node, ast.Constant) and default_node.value is None

    def _transpile_option_expr(self, node):
        """Transpile expressions that must stay Option<T>, mainly storage existence checks."""
        if isinstance(node, ast.Call) and self._is_self_storage_call(node) == 'get' and len(node.args) == 1:
            key_str = self._transpile_storage_key(node.args[0])
            key_type = self._get_storage_key_type(node.args[0]) or "soroban_sdk::Val"
            return f"env.storage().instance().get::<_, {key_type}>({key_str})"
        expr = self.transpile_expr(node)
        if expr.endswith('.clone()'):
            expr = expr[:-8]
        return expr

    def _transpile_condition(self, node):
        if isinstance(node, ast.Name) and node.id in self.option_vars:
            return f"{escape_keyword(node.id)}.is_some()"
        if self._storage_get_is_option(node):
            option_expr = self._transpile_option_expr(node)
            return f"{option_expr}.is_some()"
        
        is_val = False
        if self._is_val_typed(node):
            is_val = True
        else:
            expr_type = self.get_expr_type(node)
            if expr_type and ("Val" in expr_type or expr_type == "soroban_sdk::Val"):
                is_val = True
                
        test_str = self.transpile_expr(node)
        if is_val:
            if not test_str.startswith("bool::try_from_val"):
                clean_test = test_str[:-8] if test_str.endswith(".clone()") else test_str
                return f"bool::try_from_val(&env, &{clean_test}).unwrap()"
        return test_str

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
                elif isinstance(val, int):
                    if val > 18446744073709551615:
                        return f"{val}_u128"
                    return str(val)
                elif isinstance(val, float):
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
            elif isinstance(node.value, int):
                if coerce_to == 'U256':
                    return f'U256::from_u32(&env, {node.value})'
                if node.value > 18446744073709551615:
                    return f"{node.value}_u128"
                return str(node.value)
            elif isinstance(node.value, float):
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
                    const_value = self.const_classes[node.value.id].get(node.attr)
                    if isinstance(const_value, str):
                        escaped = const_value.replace('"', '\\"')
                        return f'Symbol::new(&env, "{escaped}")'
                    if isinstance(const_value, bool):
                        return 'true' if const_value else 'false'
                    if isinstance(const_value, int):
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
                    left = self._transpile_option_expr(node.left)
                    return f"{left}.is_none()"
                elif isinstance(node.ops[0], ast.IsNot) and isinstance(node.comparators[0], ast.Constant) and node.comparators[0].value is None:
                    left = self._transpile_option_expr(node.left)
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
                        const_value = self.const_classes[c.value.id].get(c.attr)
                        if isinstance(const_value, bool):
                            return "bool"
                        if isinstance(const_value, int):
                            return "u32"
                        if isinstance(const_value, str):
                            return "Symbol"
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
                
                # If comparing mismatched integer sizes, cast the smaller one to the larger one
                lhs_type = self.get_expr_type(node.left)
                rhs_type = self.get_expr_type(comparator)
                
                def is_int_type(t):
                    return t in ('u32', 'u64', 'u128', 'i32', 'i128')
                
                int_sizes = {
                    'u32': 1,
                    'i32': 2,
                    'u64': 3,
                    'i64': 4,
                    'u128': 5,
                    'i128': 6
                }
                
                curr_left = left
                curr_right = right
                if is_int_type(lhs_type) and is_int_type(rhs_type) and lhs_type != rhs_type:
                    # Wrap the *whole* cast in parens. A bare `x as u128 < y` makes
                    # Rust read `u128 < ...` as a generic argument list ("`<` is
                    # interpreted as a start of generic arguments").
                    if int_sizes[lhs_type] < int_sizes[rhs_type]:
                        curr_left = f"(({left}) as {rhs_type})"
                    else:
                        curr_right = f"(({right}) as {lhs_type})"

                if isinstance(op, ast.NotEq):
                    ops.append(f"{curr_left} != {curr_right}")
                elif isinstance(op, ast.Eq):
                    ops.append(f"{curr_left} == {curr_right}")
                elif isinstance(op, ast.Lt):
                    ops.append(f"{curr_left} < {curr_right}")
                elif isinstance(op, ast.LtE):
                    ops.append(f"{curr_left} <= {curr_right}")
                elif isinstance(op, ast.Gt):
                    ops.append(f"{curr_left} > {curr_right}")
                elif isinstance(op, ast.GtE):
                    ops.append(f"{curr_left} >= {curr_right}")
                else:
                    ops.append(f"{curr_left} == {curr_right}")
                left = right
            return " && ".join(ops)

        elif isinstance(node, ast.BinOp):
            static_value = eval_static_constant(node)
            if isinstance(static_value, bool):
                return 'true' if static_value else 'false'
            if isinstance(static_value, int):
                if static_value > 18446744073709551615:
                    return f"{static_value}_u128"
                return str(static_value)
            if isinstance(static_value, float):
                return str(static_value)
            if isinstance(static_value, str):
                escaped = static_value.replace('"', '\\"')
                return f'Symbol::new(&env, "{escaped}")'

            if isinstance(node.op, ast.Pow):
                left = self.transpile_expr(node.left)
                right = self.transpile_expr(node.right)
                return f"({left} as u128).pow({right} as u32)"

            left_type = self.get_expr_type(node.left)
            right_type = self.get_expr_type(node.right)
            if isinstance(node.op, ast.Add) and (
                left_type == "soroban_sdk::Bytes" or right_type == "soroban_sdk::Bytes" or
                left_type == "Bytes" or right_type == "Bytes"
            ):
                left = self.transpile_expr(node.left)
                right = self.transpile_expr(node.right)
                if left.endswith(".clone()"):
                    left = left[:-8]
                return f"{{ let mut __tmp_bytes = {left}.clone(); __tmp_bytes.append(&{right}); __tmp_bytes }}"

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

            lhs_type = self.get_expr_type(node.left)
            rhs_type = self.get_expr_type(node.right)
            if left_is_val and not right_is_val:
                lhs_type = self.get_expr_type(node.right)
            elif right_is_val and not left_is_val:
                rhs_type = self.get_expr_type(node.left)
            elif left_is_val and right_is_val:
                lhs_type = "u128"
                rhs_type = "u128"

            def is_int_type(t):
                return t in ('u32', 'u64', 'u128', 'i32', 'i128')
            
            int_sizes = {
                'u32': 1,
                'i32': 2,
                'u64': 3,
                'i64': 4,
                'u128': 5,
                'i128': 6
            }
            
            if is_int_type(lhs_type) and is_int_type(rhs_type) and lhs_type != rhs_type:
                # Fully parenthesize casts so a following operator/method call binds
                # correctly (e.g. `(x as u128) * y`, not `x as u128 * y`).
                if int_sizes[lhs_type] < int_sizes[rhs_type]:
                    left = f"(({left}) as {rhs_type})"
                else:
                    right = f"(({right}) as {lhs_type})"

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
                if isinstance(node.operand, ast.Name) and node.operand.id in self.option_vars:
                    return f"{escape_keyword(node.operand.id)}.is_none()"
                if self._storage_get_is_option(node.operand):
                    option_expr = self._transpile_option_expr(node.operand)
                    return f"{option_expr}.is_none()"
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
                    if not key_type:
                        key_type = self.get_expr_type(default_node)
                    # The default must match the resolved element type. A bare numeric
                    # literal otherwise picks the default width (u64) and mismatches a
                    # u32/u128 slot: `get::<_, u32>(..).unwrap_or(0_u64)` won't compile.
                    default_str = self._typed_literal(default_node, key_type)
                    if key_type:
                        return f"env.storage().instance().get::<_, {key_type}>({key_str}).unwrap_or({default_str})"
                    return f"env.storage().instance().get::<_, soroban_sdk::Val>({key_str}).unwrap_or({default_str})"
                else:
                    if key_type:
                        return f"env.storage().instance().get::<_, {key_type}>({key_str}).unwrap()"
                    return f"env.storage().instance().get::<_, soroban_sdk::Val>({key_str}).unwrap()"
            elif storage_method == 'has':
                key_str = self._transpile_storage_key(node.args[0])
                return f"env.storage().instance().has({key_str})"
            elif storage_method == 'set':
                # Handled in transpile_stmt, but can appear as expression.
                # Attempt to create a typed constructor for empty Map()/Vec()
                # values so Rust can infer generic parameters (Vec::<T>::new()).
                key_str = self._transpile_storage_key(node.args[0])
                val_node = node.args[1]
                val_str = self.transpile_expr(val_node)

                if (isinstance(val_node, ast.Call) and isinstance(val_node.func, ast.Name)
                    and val_node.func.id in ('Map', 'Vec')):
                    # Try to find an explicit storage type for this key
                    explicit_type = None
                    key_pat = self._extract_key_pattern(node.args[0])
                    if key_pat and key_pat in self.storage_key_types:
                        explicit_type = self.storage_key_types[key_pat]
                    # Also allow directly using the raw key name as a lookup
                    if explicit_type is None and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        k = node.args[0].value
                        explicit_type = self.storage_key_types.get(k)

                    if explicit_type:
                        try:
                            if val_node.func.id == 'Vec' and explicit_type.startswith('soroban_sdk::Vec<'):
                                inner = explicit_type[explicit_type.find('<')+1:-1]
                                typed_ctor = f"soroban_sdk::Vec::<{inner}>::new(&env)"
                                return f"env.storage().instance().set({key_str}, &({typed_ctor}))"
                            if val_node.func.id == 'Map' and explicit_type.startswith('soroban_sdk::Map<'):
                                inner = explicit_type[explicit_type.find('<')+1:-1]
                                typed_ctor = f"soroban_sdk::Map::<{inner}>::new(&env)"
                                return f"env.storage().instance().set({key_str}, &({typed_ctor}))"
                        except Exception:
                            pass

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
                arg_type = self.get_expr_type(node.args[0])
                if arg_type == "soroban_sdk::Bytes" or arg_type == "Bytes":
                    return f"env.crypto().sha256(&{arg}).into()"
                return f"env.crypto().sha256(&soroban_sdk::Bytes::try_from_val(&env, &{arg}.into_val(&env)).unwrap()).into()"
            elif x == 'crypto' and y == 'keccak256':
                    # Multiple args: concat and hash
                    args_str = ", ".join([f"{self.transpile_expr(a)}.into_val(&env)" for a in node.args])
                    return f"env.crypto().keccak256(&soroban_sdk::Bytes::try_from_val(&env, &soroban_sdk::vec![&env, {args_str}].into_val(&env)).unwrap()).into()"
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
                arg0 = self.transpile_expr(node.args[0])
                arg1 = self.transpile_expr(node.args[1])
                arg2 = self.transpile_expr(node.args[2])
                arg3 = self.transpile_expr(node.args[3])
                
                use_style_b = False
                if "current_contract" in arg2:
                    use_style_b = True
                elif "current_contract" in arg0:
                    use_style_b = False
                else:
                    token_keywords = ("token", "asset", "underlying", "coin")
                    arg0_lower = arg0.lower()
                    arg2_lower = arg2.lower()
                    arg0_has_kw = any(kw in arg0_lower for kw in token_keywords)
                    arg2_has_kw = any(kw in arg2_lower for kw in token_keywords)
                    if arg0_has_kw and not arg2_has_kw:
                        use_style_b = True
                    elif arg2_has_kw and not arg0_has_kw:
                        use_style_b = False
                    else:
                        sender_keywords = ("caller", "admin", "owner", "sender", "user", "recipient", "payee", "beneficiary")
                        arg0_is_sender = any(kw in arg0_lower for kw in sender_keywords)
                        if arg0_is_sender:
                            use_style_b = False
                        else:
                            use_style_b = False
                
                if use_style_b:
                    token = arg0
                    from_addr = arg1
                    to_addr = arg2
                else:
                    from_addr = arg0
                    to_addr = arg1
                    token = arg2
                amount = arg3
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
                return 'soroban_sdk::Map::new(&env)'
            elif func_name == 'Vec':
                return 'soroban_sdk::Vec::new(&env)'
            elif func_name == 'Bytes' and not node.args:
                return 'soroban_sdk::Bytes::new(&env)'
            elif func_name == 'Bytes' and len(node.args) == 1:
                arg_str = self.transpile_expr(node.args[0])
                if "to_xdr" in arg_str or "Bytes::" in arg_str:
                    return arg_str
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
                    arg_str = self.transpile_expr(node.args[0])
                    arg_type = self.get_expr_type(node.args[0])
                    clean_arg = arg_str[:-8] if arg_str.endswith(".clone()") else arg_str
                    # Address -> String uses the inherent `.to_string()`; there is no
                    # `String: TryFromVal<Env, Address>` impl, so try_from_val won't
                    # compile. (Common in `"prefix:" + str(addr)` storage keys.)
                    if arg_type == "Address":
                        return f"{clean_arg}.to_string()"
                    # A dynamic Val may actually hold a String, so try_from_val is valid.
                    if arg_type in ("soroban_sdk::Val", "Val"):
                        return f"soroban_sdk::String::try_from_val(&env, &{clean_arg}).unwrap()"
                    # Symbol and everything else: usable directly as a key component /
                    # already string-like. Avoid an unsupported String conversion.
                    return arg_str
                return 'Symbol::new(&env, "")'
            elif func_name == 'abs':
                if node.args:
                    arg = self.transpile_expr(node.args[0])
                    return f"({arg}).abs()"
                return "0"
            elif func_name in ('min', 'max') and len(node.args) >= 2:
                fn = 'min' if func_name == 'min' else 'max'
                left = self.transpile_expr(node.args[0])
                right = self.transpile_expr(node.args[1])
                return f"core::cmp::{fn}({left}, {right})"
            elif func_name in ('U128', 'U64', 'U32', 'I128', 'I32'):
                rust_type = {
                    'U128': 'u128',
                    'U64': 'u64',
                    'U32': 'u32',
                    'I128': 'i128',
                    'I32': 'i32'
                }[func_name]
                if node.args:
                    inner = node.args[0]
                    if isinstance(inner, ast.Constant) and isinstance(inner.value, (int, float)):
                        return f"{inner.value}_{rust_type}"
                    inner_str = self.transpile_expr(inner)
                    is_val = False
                    if self._is_val_typed(inner):
                        is_val = True
                    else:
                        t = self.get_expr_type(inner)
                        if t and ("Val" in t or t == "soroban_sdk::Val"):
                            is_val = True
                    if is_val:
                        clean_inner = inner_str[:-8] if inner_str.endswith(".clone()") else inner_str
                        return f"{rust_type}::try_from_val(&env, &{clean_inner}).unwrap()"
                    return f"(({inner_str}) as {rust_type})"
                return f"0_{rust_type}"
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

            if method == 'encode':
                base = self._get_base_str(node.func.value)
                return f"{base}.to_xdr(&env)"

            # .append() -> .push_back()
            if method == 'append':
                base_str = self._get_base_str(node.func.value)
                if isinstance(node.func.value, ast.Name):
                    var_name = node.func.value.id
                    arg_type = self.get_expr_type(node.args[0])
                    if arg_type and arg_type != "Symbol" and "Val" not in arg_type:
                        self.local_var_types[var_name] = f"soroban_sdk::Vec<{arg_type}>"
                arg_str = self.transpile_expr(node.args[0])
                # If this is a local variable with an inferred Vec type, use the
                # concrete inner type to decide whether to call into_val.
                if isinstance(node.func.value, ast.Name) and node.func.value.id in self.local_var_types:
                    base_type = self.local_var_types[node.func.value.id]
                    if base_type.startswith("soroban_sdk::Vec<"):
                        inner = base_type[17:-1]
                        if "Val" in inner:
                            return f"{base_str}.push_back({arg_str}.into_val(&env))"
                        else:
                            return f"{base_str}.push_back({arg_str})"
                # Fallback: if the value container is Val-typed, convert args to Val
                if self._is_val_typed(node.func.value):
                    return f"{base_str}.push_back({arg_str}.into_val(&env))"
                return f"{base_str}.push_back({arg_str})"

            # .require_auth()
            if method == 'require_auth':
                base = self._get_base_str(node.func.value)
                return f"{base}.require_auth()"

            # .length() -> .len()
            if method == 'length':
                base = self._get_base_str(node.func.value)
                return f"{base}.len()"

            # .len()
            if method == 'len' and not node.args:
                base = self._get_base_str(node.func.value)
                return f"{base}.len()"

            # .keys()
            if method == 'keys' and not node.args:
                base = self._get_base_str(node.func.value)
                return f"{base}.keys()"

            # .get(index) on Vec/Map
            if method == 'get' and len(node.args) == 1:
                # Skip if this is a storage or env chain (already handled above)
                if not (isinstance(node.func.value, ast.Attribute) and node.func.value.attr in ('storage', 'env')):
                    base = self._get_base_str(node.func.value)
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
                    base = self._get_base_str(node.func.value)
                    key = self.transpile_expr(node.args[0])
                    val = self.transpile_expr(node.args[1])
                    # If this is a local variable with an inferred type, prefer
                    # not converting values to Val unless the local type requires it.
                    # If this is a local variable with an inferred type, prefer
                    # not converting values to Val unless the local type requires it.
                    if isinstance(node.func.value, ast.Name) and node.func.value.id in self.local_var_types:
                        inferred = self.local_var_types[node.func.value.id]
                        if inferred.startswith("soroban_sdk::Map<"):
                            inner = inferred[17:-1]
                            parts = [p.strip() for p in inner.split(",")]
                            if len(parts) == 2:
                                k_type, v_type = parts[0], parts[1]
                                k_str = f"{key}.into_val(&env)" if "Val" in k_type else key
                                v_str = f"{val}.into_val(&env)" if "Val" in v_type else val
                                return f"{base}.set({k_str}, {v_str});"
                            return f"{base}.set({key}, {val});"
                        if inferred.startswith("soroban_sdk::Vec<"):
                            inner = inferred[17:-1]
                            v_str = f"{val}.into_val(&env)" if "Val" in inner else val
                            return f"{base}.set({key} as u32, {v_str});"

                    if self._is_vec_typed(node.func.value):
                        base_type = self.get_expr_type(node.func.value)
                        if base_type and "Val" in base_type:
                            return f"{base}.set({key} as u32, {val}.into_val(&env));"
                        return f"{base}.set({key} as u32, {val});"
                    if self._is_val_typed(node.func.value):
                        return f"{base}.set({key}.into_val(&env), {val}.into_val(&env));"
                    return f"{base}.set({key}, {val});"

            # .has(key) on Map
            if method == 'has' and len(node.args) == 1:
                if not (isinstance(node.func.value, ast.Attribute) and node.func.value.attr in ('storage',)):
                    base = self._get_base_str(node.func.value)
                    key = self.transpile_expr(node.args[0])
                    return f"{base}.has({key})"

            # .remove(index/key)
            if method == 'remove' and len(node.args) == 1:
                if not (isinstance(node.func.value, ast.Attribute) and node.func.value.attr in ('storage',)):
                    base = self._get_base_str(node.func.value)
                    arg = self.transpile_expr(node.args[0])
                    if self._is_vec_typed(node.func.value):
                        return f"{base}.remove({arg} as u32)"
                    return f"{base}.remove({arg})"

            # .push_back()
            if method == 'push_back':
                base = self._get_base_str(node.func.value)
                arg = self.transpile_expr(node.args[0])
                if self._is_val_typed(node.func.value):
                    return f"{base}.push_back({arg}.into_val(&env))"
                return f"{base}.push_back({arg})"

            # .to_bytes()
            if method == 'to_bytes' and not node.args:
                base = self._get_base_str(node.func.value)
                return f"soroban_sdk::Bytes::try_from_val(&env, &{base}.into_val(&env)).unwrap()"

            # .to_string()
            if method == 'to_string' and not node.args:
                base = self._get_base_str(node.func.value)
                return f"soroban_sdk::String::try_from_val(&env, &{base}.into_val(&env)).unwrap()"

            # .concat()
            if method == 'concat' and len(node.args) == 1:
                base = self._get_base_str(node.func.value)
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
                base = self._get_base_str(node.func.value)
                return f"env.invoke_contract::<soroban_sdk::Val>(&{base}, &Symbol::new(&env, \"latest_round_data\"), soroban_sdk::vec![&env])"

            # .deploy() on deployer
            if method == 'deploy':
                base = self._get_base_str(node.func.value)
                if node.args:
                    args_str = ", ".join([self.transpile_expr(a) for a in node.args])
                    return f"{base}.deploy({args_str})"
                return f"{base}.deploy()"

            # .upload_contract_wasm()
            if method == 'upload_contract_wasm':
                base = self._get_base_str(node.func.value)
                arg = self.transpile_expr(node.args[0])
                return f"{base}.upload_contract_wasm({arg})"

            # .with_current_contract()
            if method == 'with_current_contract':
                base = self._get_base_str(node.func.value)
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
            # `self.SomeEvent(...)` where SomeEvent is an @event class is an event
            # emission, not a contract method call. Without this it falls through to
            # the `Self::SomeEvent(...)` path, which references a nonexistent
            # associated item ("no associated item named `X` found for struct ...").
            if func_name in self.events:
                is_event = True

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
            target_func = None
            for f in self.functions_meta:
                if f["name"] == method_name:
                    target_func = f
                    break
            
            expected_types = []
            if target_func:
                for arg_name, arg_type in target_func["args"]:
                    if arg_name == "self" or arg_type == "Env":
                        continue
                    expected_types.append(map_type(arg_type))
            
            transpiled_args = []
            for i, arg_node in enumerate(node.args):
                arg_str = self.transpile_expr(arg_node)
                if target_func and i < len(expected_types):
                    expected_t = expected_types[i]
                    if expected_t not in ("Val", "soroban_sdk::Val"):
                        actual_is_val = False
                        if self._is_val_typed(arg_node):
                            actual_is_val = True
                        else:
                            act_t = self.get_expr_type(arg_node)
                            if act_t and ("Val" in act_t or act_t == "soroban_sdk::Val"):
                                actual_is_val = True
                        if actual_is_val:
                            expected_t_tf = to_turbofish(expected_t)
                            if not arg_str.startswith(f"{expected_t_tf}::try_from_val"):
                                clean_arg = arg_str[:-8] if arg_str.endswith(".clone()") else arg_str
                                arg_str = f"{expected_t_tf}::try_from_val(&env, &{clean_arg}).unwrap()"
                transpiled_args.append(arg_str)
                
            args_list = ["env.clone()"] + transpiled_args + [self.transpile_expr(k.value) for k in node.keywords]
            return f"Self::{method_name}({', '.join(args_list)})"

        # ── 8. Fallback: generic function call ──
        func_str = self.transpile_expr(node.func)
        args_list = [self.transpile_expr(a) for a in node.args] + [self.transpile_expr(k.value) for k in node.keywords]
        args_str = ", ".join(args_list)
        return f"{func_str}({args_str})"

    # ── Expression type inference ──────────────────────────────────────

    def _infer_key_parts_types(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value.replace(':', '_').strip('_')
            if s:
                return ["Symbol"]
            return []
        elif isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant):
                    s = str(v.value).replace(':', '_').strip('_')
                    if s:
                        parts.append("Symbol")
                elif isinstance(v, ast.FormattedValue):
                    t = self.get_expr_type(v.value)
                    parts.append(map_type(t) if t else "Symbol")
            return parts
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self._infer_key_parts_types(node.left) + self._infer_key_parts_types(node.right)
        else:
            t = self.get_expr_type(node)
            return [map_type(t) if t else "Symbol"]

    def get_expr_type(self, node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return "bool"
            elif isinstance(node.value, (int, float)):
                return None
            elif isinstance(node.value, str):
                return "Symbol"
        elif isinstance(node, ast.Call):
            func_name = None
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == 'self':
                func_name = node.func.attr
            elif isinstance(node.func, ast.Name):
                func_name = node.func.id

            if func_name:
                for f in self.functions_meta:
                    if f["name"] == func_name:
                        ret_type = f.get("returns")
                        if ret_type and ret_type != "None":
                            return map_type(ret_type)

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
                elif name == "str":
                    if node.args:
                        return self.get_expr_type(node.args[0])
                    return "Symbol"
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
            if isinstance(node.value, ast.Name) and node.value.id in self.const_classes:
                const_value = self.const_classes[node.value.id].get(node.attr)
                if isinstance(const_value, bool):
                    return "bool"
                if isinstance(const_value, int):
                    return "u32"
                if isinstance(const_value, str):
                    return "Symbol"
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
            if isinstance(node.op, ast.Add) and (
                (isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)) or \
                (isinstance(node.right, ast.Constant) and isinstance(node.right.value, str)) or \
                isinstance(node.left, ast.JoinedStr) or isinstance(node.right, ast.JoinedStr)
            ):
                parts_types = self._infer_key_parts_types(node)
                if len(parts_types) == 1:
                    return parts_types[0]
                elif len(parts_types) > 1:
                    return f"({', '.join(parts_types)})"
            left = self.get_expr_type(node.left)
            right = self.get_expr_type(node.right)
            res_type = None
            if left != "Symbol" and left == right:
                res_type = left
            elif left != "Symbol":
                res_type = left
            elif right != "Symbol":
                res_type = right
            else:
                res_type = "u128"
            if res_type == "soroban_sdk::Val":
                return "u128"
            return res_type
        elif isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return "bool"
            return self.get_expr_type(node.operand)
        elif isinstance(node, ast.JoinedStr):
            parts_types = self._infer_key_parts_types(node)
            if len(parts_types) == 1:
                return parts_types[0]
            elif len(parts_types) > 1:
                return f"({', '.join(parts_types)})"
            return "Symbol"
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
            # If assigning a freshly constructed Map()/Vec() into storage, try
            # to instantiate a typed container so Rust can infer generic
            # parameters (e.g. Vec::<T>::new(&env)). Prefer explicit state
            # variable type, then inferred storage key types.
            if val_node and isinstance(val_node, ast.Call) and isinstance(val_node.func, ast.Name) and val_node.func.id in ('Map', 'Vec'):
                explicit_type = None
                # Prefer explicit state variable annotation
                if var_name in self.state_variables:
                    explicit_type = map_type(self.state_variables[var_name].get('type', ''))
                # Fall back to globally inferred storage key types
                if explicit_type is None and var_name in self.storage_key_types:
                    explicit_type = self.storage_key_types[var_name]

                if explicit_type:
                    # Handle Vec and Map separately
                    try:
                        if val_node.func.id == 'Vec' and explicit_type.startswith('soroban_sdk::Vec'):
                            inner = explicit_type[explicit_type.find('<')+1:-1]
                            typed_ctor = f"soroban_sdk::Vec::<{inner}>::new(&env)"
                            return f'env.storage().instance().set(&Symbol::new(&env, "{var_name}"), &({typed_ctor}));'
                        if val_node.func.id == 'Map' and explicit_type.startswith('soroban_sdk::Map'):
                            inner = explicit_type[explicit_type.find('<')+1:-1]
                            typed_ctor = f"soroban_sdk::Map::<{inner}>::new(&env)"
                            return f'env.storage().instance().set(&Symbol::new(&env, "{var_name}"), &({typed_ctor}));'
                    except Exception:
                        pass

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
                # Preserve previously inferred (more specific) local types from
                # the storage/type inferrer. Only set if we don't already have
                # a more specific type recorded.
                if target.id not in self.local_var_types:
                    self.local_var_types[target.id] = expr_type
                self.local_var_exprs[target.id] = val_node
                # Sync the inner type of an *already-Option* var to the storage get's
                # resolved element type, so the pre-declaration (`let mut x: Option<T>`)
                # matches the actual `get::<_, T>(...)` element type instead of Val.
                #
                # IMPORTANT: only adjust the inner type here — do NOT newly promote a
                # var to Option based on the assignment alone. Whether a var is Option
                # is decided by NoneComparisonCollector (is-None / `not x` / `if x:`
                # usage); promoting every no-default get would wrongly wrap vars that
                # are used as concrete values (e.g. `x = get(k)` then `x + 1`).
                target_is_option = (
                    target.id in self.option_vars
                    or self.local_var_types.get(target.id, "").startswith("Option<")
                )
                if target_is_option and self._storage_get_is_option(val_node):
                    key_type = self._get_storage_key_type(val_node.args[0])
                    inner_type = key_type or "soroban_sdk::Val"
                    self.option_vars[target.id] = inner_type
                    self.local_var_types[target.id] = f"Option<{inner_type}>"
                # Coerce a bare integer literal assigned to a U256 var into a real
                # U256 value. The target may be U256 even when the RHS literal's own
                # type is a plain integer (e.g. `bonus: U256; bonus = 20`).
                target_is_u256 = self.local_var_types.get(target.id) == 'U256'
                if (expr_type == 'U256' or target_is_u256) and isinstance(val_node, ast.Constant) and isinstance(val_node.value, (int, float)):
                    val_str = f'U256::from_u32(&env, {val_node.value})'
            
            # Wrap in Some() if it's option-wrapped
            if target.id in self.option_vars:
                if val_node:
                    val_str = self._transpile_option_expr(val_node)
                rhs_is_option = False
                if isinstance(val_node, ast.Name) and val_node.id in self.option_vars:
                    rhs_is_option = True
                elif isinstance(val_node, ast.Call) and self._is_self_storage_call(val_node) == 'get':
                    rhs_is_option = True
                
                if not rhs_is_option and val_str != "None" and not val_str.startswith("Some("):
                    val_str = f"Some({val_str})"

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

        # ── Try/except ──
        if isinstance(node, ast.Try):
            # Rust/Soroban has no Python-style exceptions for contract calls. Emit the
            # protected body and ignore exception handlers instead of outputting Python.
            body_stmts = [self.transpile_stmt(s) for s in node.body]
            return "\n        ".join(body_stmts) if body_stmts else "// no-op try"

        # ── While loop ──
        if isinstance(node, ast.While):
            test_str = self._transpile_condition(node.test)
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
            test_str = self._transpile_condition(node.test)
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

    def add(name):
        # `_` is a Python throwaway target (e.g. `for _ in range(n)` or `_ = expr`).
        # It must never be declared as a Rust `let mut` binding — `let mut _` is a
        # syntax error. The for-loop / assignment codegen emits `_` directly where
        # valid, so simply skip collecting it here.
        if name != '_':
            local_vars.add(name)

    for node in ast.walk(func_node):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    add(target.id)
                elif isinstance(target, (ast.Tuple, ast.List)):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            add(elt.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                add(node.target.id)
        elif isinstance(node, ast.For):
            if isinstance(node.target, ast.Name):
                add(node.target.id)
            elif isinstance(node.target, (ast.Tuple, ast.List)):
                for elt in node.target.elts:
                    if isinstance(elt, ast.Name):
                        add(elt.id)
    return local_vars
