import ast
from .utils import map_type

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
                
                prev_state = -1
                iterations = 0
                while (len(self.local_var_types) + len(self.storage_key_types)) > prev_state and iterations < 5:
                    prev_state = len(self.local_var_types) + len(self.storage_key_types)
                    self.visit(func_node)
                    iterations += 1
                
                self.func_local_types[func["name"]] = dict(self.local_var_types)

    def _is_self_storage_call(self, node):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            val = node.func.value
            if isinstance(val, ast.Attribute) and val.attr == 'storage':
                if isinstance(val.value, ast.Name) and val.value.id == 'self':
                    return node.func.attr
        return None

    def _is_self_env_call(self, node):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            val = node.func.value
            if isinstance(val, ast.Attribute) and val.attr == 'env':
                if isinstance(val.value, ast.Name) and val.value.id == 'self':
                    return node.func.attr
        return None

    def _get_self_env_chain(self, node):
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
                    t = self._infer_type_from_expr(v.value)
                    parts.append(map_type(t) if t else "Symbol")
            return parts
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self._infer_key_parts_types(node.left) + self._infer_key_parts_types(node.right)
        else:
            t = self._infer_type_from_expr(node)
            return [map_type(t) if t else "Symbol"]

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
                if name == "len": return "u32"
                if name == "str":
                    if node.args:
                        return self._infer_type_from_expr(node.args[0])
                    return "Symbol"
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in ('len', 'length'):
                    return "u32"
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
            
            # Handle storage gets and env chain returns
            storage_method = self._is_self_storage_call(node)
            if storage_method == 'get':
                key_type = self.storage_key_types.get(self._extract_key_pattern(node.args[0]))
                if key_type:
                    return key_type
                if len(node.args) >= 2:
                    return self._infer_type_from_expr(node.args[1])
                return None
            if storage_method == 'has':
                return "bool"
            if storage_method == 'set':
                return "()"
            
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
        if isinstance(node, ast.JoinedStr):
            parts_types = self._infer_key_parts_types(node)
            if len(parts_types) == 1:
                return parts_types[0]
            elif len(parts_types) > 1:
                return f"({', '.join(parts_types)})"
            return "Symbol"
        if isinstance(node, ast.BinOp):
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
            left_type = self._infer_type_from_expr(node.left)
            right_type = self._infer_type_from_expr(node.right)
            res_type = None
            if left_type and right_type and left_type == right_type:
                res_type = left_type
            else:
                res_type = left_type or right_type or "u128"
            if res_type == "soroban_sdk::Val":
                return "u128"
            return res_type
        if isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name):
                base_name = node.value.id
                if base_name in self.local_var_types:
                    base_type = map_type(self.local_var_types[base_name])
                    if base_type.startswith("soroban_sdk::Vec<") and base_type.endswith(">"):
                        return base_type[17:-1]
                    elif base_type.startswith("soroban_sdk::Map<") and base_type.endswith(">"):
                        parts = base_type[17:-1].split(",")
                        if len(parts) == 2:
                            return parts[1].strip()
                elif base_name in self.state_variables:
                    base_type = map_type(self.state_variables[base_name].get("type", ""))
                    if base_type.startswith("soroban_sdk::Vec<") and base_type.endswith(">"):
                        return base_type[17:-1]
                    elif base_type.startswith("soroban_sdk::Map<") and base_type.endswith(">"):
                        parts = base_type[17:-1].split(",")
                        if len(parts) == 2:
                            return parts[1].strip()
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "self":
                if node.attr in self.state_variables:
                    return map_type(self.state_variables[node.attr].get("type", ""))
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

    def _is_storage_get_call(self, node):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            val = node.func.value
            if (isinstance(val, ast.Attribute) and val.attr == 'storage' and
                isinstance(val.value, ast.Name) and val.value.id == 'self' and
                node.func.attr == 'get' and len(node.args) >= 1):
                return True
        return False

    def visit_Assign(self, node):
        """Track local variable types from dict/constructor assignments."""
        if len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                left_type = self.local_var_types.get(target.id)
                right_type = self._infer_type_from_expr(node.value)
                if right_type:
                    self.local_var_types[target.id] = right_type
                elif left_type and isinstance(node.value, ast.Name):
                    self.local_var_types[node.value.id] = left_type
                # Also track the expression for key pattern resolution
                self.local_var_exprs[target.id] = node.value

                # Propagate type from LHS variable to storage key pattern if assigning from storage get
                if left_type and left_type != "soroban_sdk::Val":
                    if self._is_storage_get_call(node.value):
                        key_node = node.value.args[0]
                        key_pattern = self._extract_key_pattern(key_node)
                        if key_pattern:
                            self.storage_key_types[key_pattern] = left_type
            elif isinstance(target, ast.Subscript):
                # Handle local map element assignment like `manifest[key] = value`
                # to refine the local Map's inner types.
                if isinstance(target.value, ast.Name):
                    base_name = target.value.id
                    # Don't override state variables
                    if base_name not in self.state_variables:
                        key_type = self._infer_type_from_expr(target.slice)
                        val_type = self._infer_type_from_expr(node.value)
                        if val_type:
                            key_rust = map_type(key_type) if key_type else 'soroban_sdk::Val'
                            val_rust = map_type(val_type) if val_type else 'soroban_sdk::Val'
                            self.local_var_types[base_name] = f"soroban_sdk::Map<{key_rust}, {val_rust}>"
                        # Also track expression for possible later resolution
                        self.local_var_exprs[base_name] = node.value

    def visit_AnnAssign(self, node):
        if isinstance(node.target, ast.Name):
            ann_type = map_type(ast.unparse(node.annotation))
            self.local_var_types[node.target.id] = ann_type
            if node.value:
                if isinstance(node.value, ast.Name):
                    self.local_var_types[node.value.id] = ann_type
        self.generic_visit(node)

    def visit_BinOp(self, node):
        left_type = self._infer_type_from_expr(node.left)
        right_type = self._infer_type_from_expr(node.right)
        is_string_concat = isinstance(node.op, ast.Add) and (
            left_type == "Symbol" or right_type == "Symbol" or
            (isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)) or
            (isinstance(node.right, ast.Constant) and isinstance(node.right.value, str))
        )
        if not is_string_concat:
            if left_type and not right_type and isinstance(node.right, ast.Name):
                self.local_var_types[node.right.id] = left_type
            elif right_type and not left_type and isinstance(node.left, ast.Name):
                self.local_var_types[node.left.id] = right_type
        self.generic_visit(node)

    def visit_Compare(self, node):
        if len(node.comparators) == 1:
            left_type = self._infer_type_from_expr(node.left)
            right_type = self._infer_type_from_expr(node.comparators[0])
            if left_type and not right_type and isinstance(node.comparators[0], ast.Name):
                self.local_var_types[node.comparators[0].id] = left_type
            elif right_type and not left_type and isinstance(node.left, ast.Name):
                self.local_var_types[node.left.id] = right_type
        self.generic_visit(node)

    def visit_Call(self, node):
        """Detect self.storage.set(key, value) calls and infer types."""
        # Check calls to contract methods to infer argument types
        func_name = None
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == 'self':
            func_name = node.func.attr
        elif isinstance(node.func, ast.Name):
            func_name = node.func.id

        if func_name:
            target_func = None
            for f in self.functions_meta:
                if f["name"] == func_name:
                    target_func = f
                    break
            if target_func:
                func_args = target_func["args"]
                if func_args and func_args[0][0] == 'self':
                    func_args = func_args[1:]
                for idx, arg_node in enumerate(node.args):
                    if idx < len(func_args) and isinstance(arg_node, ast.Name):
                        arg_name, arg_type = func_args[idx]
                        if arg_type and arg_type != 'Env':
                            self.local_var_types[arg_node.id] = map_type(arg_type)

        is_transfer = False
        is_mint_or_burn = False
        is_invoke = False
        if isinstance(node.func, ast.Attribute):
            if node.func.attr == 'transfer':
                val = node.func.value
                if isinstance(val, ast.Attribute) and val.attr == 'env' and isinstance(val.value, ast.Name) and val.value.id == 'self':
                    is_transfer = True
                elif isinstance(val, ast.Name) and val.id == 'env':
                    is_transfer = True
            elif node.func.attr in ('mint', 'burn'):
                val = node.func.value
                if isinstance(val, ast.Attribute) and val.attr == 'env' and isinstance(val.value, ast.Name) and val.value.id == 'self':
                    is_mint_or_burn = True
                elif isinstance(val, ast.Name) and val.id == 'env':
                    is_mint_or_burn = True
            elif node.func.attr in ('invoke_contract', 'call'):
                val = node.func.value
                if isinstance(val, ast.Attribute) and val.attr == 'env' and isinstance(val.value, ast.Name) and val.value.id == 'self':
                    is_invoke = True
                elif isinstance(val, ast.Name) and val.id == 'env':
                    is_invoke = True
        
        if is_transfer and len(node.args) >= 3:
            for arg in node.args[:3]:
                if isinstance(arg, ast.Name):
                    self.local_var_types[arg.id] = "Address"
        elif is_mint_or_burn and len(node.args) >= 2:
            for arg in node.args[:2]:
                if isinstance(arg, ast.Name):
                    self.local_var_types[arg.id] = "Address"
        elif is_invoke and len(node.args) >= 1:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Name):
                self.local_var_types[arg0.id] = "Address"

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

    def visit_For(self, node):
        if isinstance(node.target, ast.Name):
            # If iterating over range or length, it's u32
            if isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Name) and node.iter.func.id in ('range', 'len'):
                self.local_var_types[node.target.id] = "u32"
            elif isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Attribute) and node.iter.func.attr in ('len', 'length'):
                self.local_var_types[node.target.id] = "u32"
            else:
                collection_type = self._infer_type_from_expr(node.iter)
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
        self.generic_visit(node)
