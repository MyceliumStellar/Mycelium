from mycelium_compiler.parser import MyceliumCompilerVisitor

# Extensive primitives list covering class-based and module-based (Vyper-like) contract types
VALID_PRIMITIVES = {
    "int", "str", "bytes", "bool", "Symbol", "i128", "i32", "i64", "u32", "u64", "Bytes",
    "uint256", "uint128", "uint64", "uint32", "int256", "int128", "int64", "int32",
    "address", "String", "bool", "bytes32",
    "Address", "U128", "U64", "U32", "I128", "I32", "Bool", "Env", "U256",
    "Map", "Vec", "tuple", "list"
}

def is_valid_type(t: str, visitor: MyceliumCompilerVisitor = None) -> bool:
    if not t:
        return False
        
    t = t.strip()
    
    if t.endswith(" or None"):
        return is_valid_type(t[:-8], visitor)
        
    # 1. Base primitives check
    if t in VALID_PRIMITIVES:
        return True
        
    # 2. Check custom structs and interfaces parsed in AST visitor
    if visitor:
        if t in visitor.structs or t in visitor.interfaces:
            return True
        
    # 3. Strip standard wrappers like constant(...) or indexed(...)
    if t.startswith("constant(") and t.endswith(")"):
        return is_valid_type(t[9:-1], visitor)
    if t.startswith("indexed(") and t.endswith(")"):
        return is_valid_type(t[8:-1], visitor)
        
    # 4. Handle tuples: (uint256, uint256)
    if t.startswith("(") and t.endswith(")"):
        inner = t[1:-1]
        parts = [p.strip() for p in inner.split(",")]
        return all(is_valid_type(p, visitor) for p in parts if p)
        
    # 4.5. Handle sized Bytes: Bytes[1024]
    if t.startswith("Bytes[") and t.endswith("]"):
        return True

    # 5. Handle DynArray[address, 10]
    if t.startswith("DynArray[") and t.endswith("]"):
        inner = t[9:-1]
        parts = [p.strip() for p in inner.split(",", 1)]
        return len(parts) == 2 and is_valid_type(parts[0], visitor)

    # 6. Handle mappings and dicts
    if t.startswith("Mapping[") and t.endswith("]"):
        inner = t[8:-1]
        parts = [p.strip() for p in inner.split(",", 1)]
        return len(parts) == 2 and is_valid_type(parts[0], visitor) and is_valid_type(parts[1], visitor)
        
    if t.startswith("Map[") and t.endswith("]"):
        inner = t[4:-1]
        parts = [p.strip() for p in inner.split(",", 1)]
        return len(parts) == 2 and is_valid_type(parts[0], visitor) and is_valid_type(parts[1], visitor)
        
    if t.startswith("dict[") and t.endswith("]"):
        inner = t[5:-1]
        parts = [p.strip() for p in inner.split(",", 1)]
        return len(parts) == 2 and is_valid_type(parts[0], visitor) and is_valid_type(parts[1], visitor)

    # 7. Handle lists and vectors
    if t.startswith("List[") and t.endswith("]"):
        return is_valid_type(t[5:-1], visitor)
    if t.startswith("Vec[") and t.endswith("]"):
        return is_valid_type(t[4:-1], visitor)
    if t.startswith("list[") and t.endswith("]"):
        return is_valid_type(t[5:-1], visitor)

    return False

def validate_ast(visitor: MyceliumCompilerVisitor):
    """
    Validates that the types used in the AST fit Soroban primitives and constraints.
    """
    # Validate state variables
    for var_name, info in visitor.state_variables.items():
        var_type = info["type"]
        if not is_valid_type(var_type, visitor):
            raise TypeError(f"State variable '{var_name}' has unsupported type '{var_type}' for Soroban.")
            
    # Validate function signatures
    for func in visitor.functions:
        for arg_name, arg_type in func["args"]:
            if arg_name == "self":
                continue
            if not is_valid_type(arg_type, visitor):
                raise TypeError(f"Argument '{arg_name}' in function '{func['name']}' has unsupported type '{arg_type}'.")
        
        returns = func["returns"]
        if returns != "None" and not is_valid_type(returns, visitor):
             raise TypeError(f"Function '{func['name']}' has unsupported return type '{returns}'.")
             
    return True
