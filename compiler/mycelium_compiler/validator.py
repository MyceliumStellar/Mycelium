from mycelium_compiler.parser import MyceliumCompilerVisitor

VALID_PRIMITIVES = {"int", "str", "bytes", "bool", "Symbol", "i128", "i32", "i64", "u32", "u64"}

def validate_ast(visitor: MyceliumCompilerVisitor):
    """
    Validates that the types used in the AST fit Soroban primitives and constraints.
    """
    # Validate state variables
    for var_name, info in visitor.state_variables.items():
        var_type = info["type"]
        if var_type not in VALID_PRIMITIVES and not (var_type.startswith("dict[") or var_type.startswith("list[")):
            raise TypeError(f"State variable '{var_name}' has unsupported type '{var_type}' for Soroban.")
            
    # Validate function signatures
    for func in visitor.functions:
        for arg_name, arg_type in func["args"]:
            if arg_name == "self":
                continue
            if arg_type not in VALID_PRIMITIVES:
                raise TypeError(f"Argument '{arg_name}' in function '{func['name']}' has unsupported type '{arg_type}'.")
        
        returns = func["returns"]
        if returns != "None" and returns not in VALID_PRIMITIVES:
             raise TypeError(f"Function '{func['name']}' has unsupported return type '{returns}'.")
             
    return True
