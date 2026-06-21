import ast

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


def eval_static_constant(node):
    """Evaluate side-effect-free constant expressions used in contract fixtures."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('U64', 'U128', 'U32', 'I128', 'I32', 'Bool', 'Symbol'):
        if node.args:
            return eval_static_constant(node.args[0])
        return None
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp):
        value = eval_static_constant(node.operand)
        if value is None:
            return None
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return value
        if isinstance(node.op, ast.Not):
            return not value
        return None
    if isinstance(node, ast.BinOp):
        left = eval_static_constant(node.left)
        right = eval_static_constant(node.right)
        if left is None or right is None:
            return None
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
            if isinstance(node.op, ast.Div):
                return left // right if isinstance(left, int) and isinstance(right, int) else left / right
            if isinstance(node.op, ast.Mod):
                return left % right
            if isinstance(node.op, ast.Pow):
                return left ** right
        except Exception:
            return None
    return None


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


def _get_map_key_type(type_str: str):
    """Return the key type part for mapping-like type strings such as
    Map[Key, Value] or Mapping[Key, Value]. Returns None if not a mapping.
    """
    if not type_str:
        return None
    ts = type_str.strip()
    if ts.startswith("Map[") and ts.endswith("]"):
        inner = ts[4:-1]
        parts = [p.strip() for p in inner.split(",", 1)]
        return parts[0] if parts else None
    if ts.startswith("Mapping[") and ts.endswith("]"):
        inner = ts[8:-1]
        parts = [p.strip() for p in inner.split(",", 1)]
        return parts[0] if parts else None
    if ts.startswith("dict[") and ts.endswith("]"):
        inner = ts[5:-1]
        parts = [p.strip() for p in inner.split(",", 1)]
        return parts[0] if parts else None
    return None


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
