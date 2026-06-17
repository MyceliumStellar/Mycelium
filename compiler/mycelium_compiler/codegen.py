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


def map_type(type_str: str) -> str:
    type_str = type_str.strip()
    if type_str == "uint256" or type_str == "u256" or type_str == "int":
        return "U256"
    elif type_str == "address":
        return "Address"
    elif type_str == "bytes32":
        return "soroban_sdk::BytesN<32>"
    elif type_str == "String":
        return "Symbol"
    elif type_str == "bool":
        return "bool"
    
    if type_str.startswith("indexed(") and type_str.endswith(")"):
        return map_type(type_str[8:-1])
        
    if type_str.startswith("Bytes[") and type_str.endswith("]"):
        return "soroban_sdk::Bytes"
        
    if type_str.startswith("DynArray[") and type_str.endswith("]"):
        inner = type_str[9:-1]
        parts = [p.strip() for p in inner.rsplit(",", 1)]
        return f"soroban_sdk::Vec<{map_type(parts[0])}>"
        
    if type_str.startswith("Mapping[") and type_str.endswith("]"):
        inner = type_str[8:-1]
        parts = [p.strip() for p in inner.split(",", 1)]
        return f"soroban_sdk::Map<{map_type(parts[0])}, {map_type(parts[1])}>"
        
    if type_str.startswith("(") and type_str.endswith(")"):
        inner = type_str[1:-1]
        parts = [map_type(p.strip()) for p in inner.split(",")]
        return f"({', '.join(parts)})"
        
    return type_str


def get_mapping_leaf_type(type_str: str) -> str:
    type_str = type_str.strip()
    while type_str.startswith("Mapping[") and type_str.endswith("]"):
        inner = type_str[8:-1]
        parts = [p.strip() for p in inner.split(",", 1)]
        type_str = parts[1]
    return type_str


def flatten_subscript(node):
    keys = []
    curr = node
    while isinstance(curr, ast.Subscript):
        keys.insert(0, curr.slice)
        curr = curr.value
    if isinstance(curr, ast.Attribute) and isinstance(curr.value, ast.Name) and curr.value.id == 'self':
        return curr.attr, keys
    return None, []


def check_keyword_usage(func_node, keyword):
    for node in ast.walk(func_node):
        if isinstance(node, ast.Name) and node.id == keyword:
            return True
    return False


class RustTranspiler(ast.NodeVisitor):
    def __init__(self, state_variables, contract_name, events, local_var_types=None, return_type=None, functions_meta=None):
        self.state_variables = state_variables
        self.contract_name = contract_name
        self.events = events
        self.local_vars = set()
        self.local_var_types = local_var_types or {}
        self.return_type = return_type  # Mapped Rust return type for the current function
        self.functions_meta = functions_meta or []  # List of function dicts from visitor

    def _get_function_return_type(self, func_name):
        """Look up the mapped return type of a contract function by name."""
        for f in self.functions_meta:
            if f['name'] == func_name:
                ret = f.get('returns', 'None')
                if ret != 'None':
                    return map_type(ret)
        return None

    def _coerce_to_u256(self, node):
        """Wrap a non-U256 expression for use in U256 arithmetic.
        Parenthesizes complex expressions before casting."""
        expr_str = self.transpile_expr(node)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return f"U256::from_u32(&env, {node.value})"
        # For complex expressions (BinOp, Call, etc.), parenthesize before cast
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
                leaf_type = get_mapping_leaf_type(var_type)
                return map_type(leaf_type) == "U256"
        elif isinstance(node, ast.BinOp):
            return self.is_u256_type(node.left) or self.is_u256_type(node.right)
        elif isinstance(node, ast.Call):
            # Check if it's a self.method() call and look up the return type
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == 'self':
                ret_type = self._get_function_return_type(node.func.attr)
                return ret_type == "U256"
        return False

    def transpile_expr(self, node, coerce_to=None):
        """Transpile a Python AST expression node to Rust.
        
        Args:
            coerce_to: If set (e.g. 'U256'), coerce literals to that type.
        """
        if isinstance(node, ast.Name):
            if node.id == 'True':
                return 'true'
            elif node.id == 'False':
                return 'false'
            
            # Check if it should be cloned (excluding env)
            name = escape_keyword(node.id)
            if node.id != "env" and node.id in self.local_var_types:
                t = self.local_var_types[node.id]
                if t not in ("bool", "u32", "u64", "i32", "i64", "i128", "u8", "u16", "i8", "i16"):
                    return f"{name}.clone()"
            return name
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return 'true' if node.value else 'false'
            elif isinstance(node.value, (int, float)):
                # Coerce integer literals to U256 when context requires it
                if coerce_to == 'U256':
                    return f'U256::from_u32(&env, {node.value})'
                return str(node.value)
            elif isinstance(node.value, str):
                return f'Symbol::new(&env, "{node.value}")'
            return str(node.value)
        elif isinstance(node, ast.Tuple):
            # Transpile each element of the tuple
            elements = [self.transpile_expr(elt) for elt in node.elts]
            return f"({', '.join(elements)})"
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == 'self':
                var_name = node.attr
                # Get variable type
                var_info = self.state_variables.get(var_name, {})
                var_type = var_info.get("type", "Symbol")
                rust_type = map_type(var_type)
                
                # Generate storage lookup
                get_expr = f'env.storage().instance().get::<_, {rust_type}>(&Symbol::new(&env, "{var_name}"))'
                if rust_type == "U256":
                    return f"{get_expr}.unwrap_or_else(|| U256::from_u32(&env, 0))"
                elif rust_type in ("i128", "bool", "u64", "u32", "i64", "i32"):
                    return f"{get_expr}.unwrap_or_default()"
                else:
                    return f"{get_expr}.unwrap()"
            else:
                return f"{self.transpile_expr(node.value)}.{node.attr}"
        elif isinstance(node, ast.Subscript):
            attr, keys = flatten_subscript(node)
            if attr:
                var_info = self.state_variables.get(attr, {})
                var_type = var_info.get("type", "Symbol")
                leaf_type = get_mapping_leaf_type(var_type)
                leaf_rust_type = map_type(leaf_type)
                
                transpiled_keys = [self.transpile_expr(k) for k in keys]
                key_tuple_elements = [f'Symbol::new(&env, "{attr}")'] + transpiled_keys
                key_tuple_str = f"({', '.join(key_tuple_elements)},)" if len(key_tuple_elements) == 1 else f"({', '.join(key_tuple_elements)})"
                
                get_expr = f"env.storage().instance().get::<_, {leaf_rust_type}>(&{key_tuple_str})"
                if leaf_rust_type == "U256":
                    return f"{get_expr}.unwrap_or_else(|| U256::from_u32(&env, 0))"
                elif leaf_rust_type in ("U256", "i128", "bool", "u64", "u32", "i64", "i32"):
                    return f"{get_expr}.unwrap_or_default()"
                else:
                    return f"{get_expr}.unwrap()"
            else:
                return ast.unparse(node)
        elif isinstance(node, ast.Call):
            is_event = False
            func_name = None
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
                if func_name in self.events or func_name[0].isupper():
                    is_event = True
            elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == 'self':
                func_name = node.func.attr
                if func_name in self.events or func_name[0].isupper():
                    is_event = True
                    
            if is_event and func_name:
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
                
                if len(data) == 0:
                    data_str = "()"
                elif len(data) == 1:
                    data_str = data[0]
                else:
                    data_str = f"({', '.join(data)})"
                return f"env.events().publish({topics_str}, &{data_str})"
            
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == 'self':
                method_name = node.func.attr
                args_list = ["env.clone()"] + [self.transpile_expr(a) for a in node.args]
                return f"Self::{method_name}({', '.join(args_list)})"
            
            func_str = self.transpile_expr(node.func)
            args_str = ", ".join([self.transpile_expr(a) for a in node.args])
            return f"{func_str}({args_str})"
        elif isinstance(node, ast.Compare):
            any_u256 = self.is_u256_type(node.left) or any(self.is_u256_type(c) for c in node.comparators)
            
            def transpile_comparator(c):
                is_u = self.is_u256_type(c)
                if any_u256 and not is_u:
                    return self._coerce_to_u256(c)
                return self.transpile_expr(c)
            
            left = transpile_comparator(node.left)
            ops = []
            for op, comparator in zip(node.ops, node.comparators):
                right = transpile_comparator(comparator)
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
            return " && ".join(ops)
        elif isinstance(node, ast.BinOp):
            left_is_u256 = self.is_u256_type(node.left)
            right_is_u256 = self.is_u256_type(node.right)
            
            if left_is_u256 or right_is_u256:
                if not left_is_u256:
                    left = self._coerce_to_u256(node.left)
                else:
                    left = self.transpile_expr(node.left)
                
                if not right_is_u256:
                    right = self._coerce_to_u256(node.right)
                else:
                    right = self.transpile_expr(node.right)
                
                if isinstance(node.op, ast.Add):
                    return f"{left}.add(&{right})"
                elif isinstance(node.op, ast.Sub):
                    return f"{left}.sub(&{right})"
                elif isinstance(node.op, ast.Mult):
                    return f"{left}.mul(&{right})"
                elif isinstance(node.op, ast.Div):
                    return f"{left}.div(&{right})"
                elif isinstance(node.op, ast.Mod):
                    return f"{left}.rem_euclid(&{right})"
            
            left = self.transpile_expr(node.left)
            right = self.transpile_expr(node.right)
            op_str = "+"
            if isinstance(node.op, ast.Add):
                op_str = "+"
            elif isinstance(node.op, ast.Sub):
                op_str = "-"
            elif isinstance(node.op, ast.Mult):
                op_str = "*"
            elif isinstance(node.op, ast.Div):
                op_str = "/"
            elif isinstance(node.op, ast.Mod):
                op_str = "%"
            return f"{left} {op_str} {right}"
        elif isinstance(node, ast.UnaryOp):
            operand = self.transpile_expr(node.operand)
            if isinstance(node.op, ast.Not):
                return f"!{operand}"
            elif isinstance(node.op, ast.USub):
                return f"-{operand}"
            return operand
        elif isinstance(node, ast.BoolOp):
            values = [self.transpile_expr(v) for v in node.values]
            op_str = " && " if isinstance(node.op, ast.And) else " || "
            return op_str.join(values)
        elif isinstance(node, ast.IfExp):
            # Python ternary: val_if_true if condition else val_if_false
            test_str = self.transpile_expr(node.test)
            body_str = self.transpile_expr(node.body)
            else_str = self.transpile_expr(node.orelse)
            return f"if {test_str} {{ {body_str} }} else {{ {else_str} }}"
        return ast.unparse(node)

    def get_expr_type(self, node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return "bool"
            elif isinstance(node.value, (int, float)):
                return "U256"
            elif isinstance(node.value, str):
                return "Symbol"
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
                return map_type(get_mapping_leaf_type(var_info.get("type", "")))
        return "Symbol"

    def transpile_stmt(self, node):
        if isinstance(node, ast.Assign):
            target = node.targets[0]
            val_str = self.transpile_expr(node.value)
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'self':
                var_name = target.attr
                return f'env.storage().instance().set(&Symbol::new(&env, "{var_name}"), &({val_str}));'
            elif isinstance(target, ast.Subscript):
                attr, keys = flatten_subscript(target)
                if attr:
                    transpiled_keys = [f"{self.transpile_expr(k)}.clone()" for k in keys]
                    key_tuple_elements = [f'Symbol::new(&env, "{attr}")'] + transpiled_keys
                    key_tuple_str = f"({', '.join(key_tuple_elements)},)" if len(key_tuple_elements) == 1 else f"({', '.join(key_tuple_elements)})"
                    return f"env.storage().instance().set(&{key_tuple_str}, &({val_str}));"
            elif isinstance(target, ast.Name):
                var_name = escape_keyword(target.id)
                expr_type = self.get_expr_type(node.value)
                self.local_var_types[target.id] = expr_type
                # Coerce integer literals to U256 if needed
                if expr_type == 'U256' and isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)):
                    val_str = f'U256::from_u32(&env, {node.value.value})'
                if target.id not in self.local_vars:
                    self.local_vars.add(target.id)
                    return f"let mut {var_name} = {val_str};"
                else:
                    return f"{var_name} = {val_str};"
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            annotated_type = map_type(ast.unparse(node.annotation))
            # Coerce literal values to annotated type
            if node.value and annotated_type == 'U256' and isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)):
                val_str = f'U256::from_u32(&env, {node.value.value})'
            else:
                val_str = self.transpile_expr(node.value) if node.value else "Default::default()"
            
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'self':
                var_name = target.attr
                return f'env.storage().instance().set(&Symbol::new(&env, "{var_name}"), &({val_str}));'
            elif isinstance(target, ast.Subscript):
                attr, keys = flatten_subscript(target)
                if attr:
                    transpiled_keys = [f"{self.transpile_expr(k)}.clone()" for k in keys]
                    key_tuple_elements = [f'Symbol::new(&env, "{attr}")'] + transpiled_keys
                    key_tuple_str = f"({', '.join(key_tuple_elements)},)" if len(key_tuple_elements) == 1 else f"({', '.join(key_tuple_elements)})"
                    return f"env.storage().instance().set(&{key_tuple_str}, &({val_str}));"
            elif isinstance(target, ast.Name):
                var_name = escape_keyword(target.id)
                self.local_var_types[target.id] = annotated_type
                if target.id not in self.local_vars:
                    self.local_vars.add(target.id)
                    return f"let mut {var_name}: {annotated_type} = {val_str};"
                else:
                    return f"{var_name} = {val_str};"
        elif isinstance(node, ast.AugAssign):
            target = node.target
            synthetic_binop = ast.BinOp(left=target, op=node.op, right=node.value)
            val_str = self.transpile_expr(synthetic_binop)
            
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'self':
                var_name = target.attr
                return f'env.storage().instance().set(&Symbol::new(&env, "{var_name}"), &({val_str}));'
            elif isinstance(target, ast.Subscript):
                attr, keys = flatten_subscript(target)
                if attr:
                    transpiled_keys = [f"{self.transpile_expr(k)}.clone()" for k in keys]
                    key_tuple_elements = [f'Symbol::new(&env, "{attr}")'] + transpiled_keys
                    key_tuple_str = f"({', '.join(key_tuple_elements)},)" if len(key_tuple_elements) == 1 else f"({', '.join(key_tuple_elements)})"
                    return f"env.storage().instance().set(&{key_tuple_str}, &({val_str}));"
            elif isinstance(target, ast.Name):
                var_name = escape_keyword(target.id)
                return f"{var_name} = {val_str};"
        elif isinstance(node, ast.Return):
            if node.value:
                # Coerce return value to U256 if the function returns U256 and the value is a literal
                if self.return_type == 'U256' and isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)):
                    return f"return U256::from_u32(&env, {node.value.value});"
                return f"return {self.transpile_expr(node.value)};"
            return "return;"
        elif isinstance(node, ast.If):
            test_str = self.transpile_expr(node.test)
            body_stmts = [self.transpile_stmt(s) for s in node.body]
            body_str = "\n        ".join(body_stmts)
            orelse_str = ""
            if node.orelse:
                orelse_stmts = [self.transpile_stmt(s) for s in node.orelse]
                orelse_str = " else {\n        " + "\n        ".join(orelse_stmts) + "\n    }"
            return f"if {test_str} {{\n        {body_str}\n    }}{orelse_str}"
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
        elif isinstance(node, ast.Pass):
            return "();"
        elif isinstance(node, ast.Expr):
            expr_str = self.transpile_expr(node.value)
            if not expr_str.endswith(";"):
                expr_str += ";"
            return expr_str
        return ast.unparse(node)


def generate_rust_intermediate(visitor: MyceliumCompilerVisitor) -> str:
    """
    Translates the validated Python AST into Soroban Rust code.
    """
    lines = [
        "#![no_std]",
        "use soroban_sdk::{contract, contractimpl, Env, Symbol, Address, U256};",
        "",
        "#[contract]",
        f"pub struct {visitor.contract_name};",
        "",
        "#[contractimpl]",
        f"impl {visitor.contract_name} {{"
    ]
    
    # Generate implementation methods
    for func in visitor.functions:
        func_node = func.get("node")
        
        # Determine parameter additions
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
            safe_name = escape_keyword(arg_name)
            args_list.append(f"{safe_name}: {map_type(arg_type)}")
        
        args_list.extend(extra_args)
        args_str = ", ".join(args_list)
            
        ret_type = ""
        mapped_ret_type = None
        if func["returns"] != "None":
            mapped_ret_type = map_type(func['returns'])
            ret_type = f" -> {mapped_ret_type}"
            
        lines.append(f"    pub fn {func['name']}({args_str}){ret_type} {{")
        
        # Inject global emulation bindings at the beginning of the body
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
        for arg_name, arg_type in func["args"]:
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
            
        transpiler = RustTranspiler(visitor.state_variables, visitor.contract_name, visitor.events, local_var_types, return_type=mapped_ret_type, functions_meta=visitor.functions)
        body_lines = []
        if func_node and hasattr(func_node, "body"):
            for stmt in func_node.body:
                body_lines.append("        " + transpiler.transpile_stmt(stmt))
        else:
            body_lines.append("        // Default return fallback")
            
        lines.extend(body_prefix)
        lines.extend(body_lines)
        lines.append("    }")
        
    lines.append("}")
    return "\n".join(lines)


def generate_wasm(visitor: MyceliumCompilerVisitor) -> bytes:
    """
    Generate a valid Soroban-compatible WASM binary from parsed contract AST
    by transpiling to Soroban Rust and invoking cargo/stellar CLI.
    """
    rust_code = generate_rust_intermediate(visitor)
    
    # Create a unique temporary directory
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
        # Write Cargo.toml
        with open(os.path.join(temp_dir, "Cargo.toml"), "w") as f:
            f.write(cargo_toml)
            
        # Write src/lib.rs
        with open(os.path.join(temp_dir, "src", "lib.rs"), "w") as f:
            f.write(rust_code)
            
        # Run stellar contract build --optimize
        cmd = ["stellar", "contract", "build", "--manifest-path", os.path.join(temp_dir, "Cargo.toml"), "--optimize"]
        
        env = os.environ.copy()
        env["CARGO_TARGET_DIR"] = "/tmp/mycelium_cargo_target"
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
        
        if res.returncode != 0:
            error_log = f"Rust Compilation Error:\n{res.stderr}\n{res.stdout}"
            print(error_log, file=sys.stderr)
            raise RuntimeError(error_log)
            
        # Locate the optimized WASM output
        target_dir = "/tmp/mycelium_cargo_target"
        wasm_path = os.path.join(target_dir, "wasm32v1-none", "release", "mycelium_contract.wasm")
        
        if not os.path.exists(wasm_path):
            wasm_path = os.path.join(target_dir, "wasm32-unknown-unknown", "release", "mycelium_contract.wasm")
            
        if not os.path.exists(wasm_path):
            raise FileNotFoundError(f"Compiled WASM not found in target directories of {target_dir}")
            
        with open(wasm_path, "rb") as f_wasm:
            wasm_bytes = f_wasm.read()
            
        return wasm_bytes
        
    finally:
        # Clean up temporary directory
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
