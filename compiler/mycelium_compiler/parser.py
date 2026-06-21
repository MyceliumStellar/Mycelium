import ast


def _eval_static_constant(node):
    """Evaluate simple module-level constant expressions safely."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('U64', 'U128', 'U32', 'I128', 'I32', 'Bool', 'Symbol'):
        if node.args:
            return _eval_static_constant(node.args[0])
        return None
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp):
        value = _eval_static_constant(node.operand)
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
        left = _eval_static_constant(node.left)
        right = _eval_static_constant(node.right)
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

class MyceliumCompilerVisitor(ast.NodeVisitor):
    def __init__(self):
        self.contract_name = None
        self.state_variables = {}
        self.functions = []
        self.events = {}
        self.interfaces = {}
        self.structs = {}
        self.errors = {}
        self.const_classes = {}  # className -> {variantName: value}
        self.class_mode = False
        self.module_constants = {}

    def parse(self, tree):
        # 0. Parse module-level constants
        self.module_constants = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name):
                    value = _eval_static_constant(node.value)
                    if value is not None:
                        self.module_constants[target.id] = value

        # 1. First Pass: Detect if there is a class decorated with @contract
        contract_class_node = None
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                has_contract = any(
                    (isinstance(d, ast.Name) and d.id == 'contract') or
                    (isinstance(d, ast.Attribute) and d.attr == 'contract')
                    for d in node.decorator_list
                )
                if has_contract:
                    contract_class_node = node
                    break
        
        # 2. Set mode and compile
        if contract_class_node:
            self.class_mode = True
            self.contract_name = contract_class_node.name
            
            # Parse only within the @contract class
            for item in contract_class_node.body:
                if isinstance(item, ast.AnnAssign):
                    self.parse_state_var(item)
                elif isinstance(item, ast.FunctionDef):
                    self.parse_function(item)
        else:
            self.class_mode = False
            self.contract_name = "ModuleContract"
            
            # Parse module-level variables and functions
            for item in tree.body:
                if isinstance(item, ast.AnnAssign):
                    self.parse_state_var(item)
                elif isinstance(item, ast.FunctionDef):
                    # Check if decorated with external/view/public/etc
                    has_func_decorator = any(
                        isinstance(d, ast.Name) and d.id in ('external', 'view', 'public', 'internal')
                        for d in item.decorator_list
                    )
                    if has_func_decorator or not item.decorator_list:
                        self.parse_function(item)

        # 3. Parse auxiliary helper classes (events, interfaces, structs) at the module level
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node != contract_class_node:
                is_event = any(isinstance(d, ast.Name) and d.id == 'event' for d in node.decorator_list)
                is_interface = any(isinstance(d, ast.Name) and d.id == 'interface' for d in node.decorator_list)
                
                class_meta = {
                    "name": node.name,
                    "fields": {}
                }
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        class_meta["fields"][item.target.id] = ast.unparse(item.annotation)
                
                if node.name == 'ContractError':
                    error_meta = {
                        "name": "ContractError",
                        "fields": {}
                    }
                    for item in node.body:
                        if isinstance(item, ast.Assign) and len(item.targets) == 1:
                            target = item.targets[0]
                            if isinstance(target, ast.Name) and isinstance(item.value, ast.Constant):
                                error_meta["fields"][target.id] = item.value.value
                    self.errors = error_meta
                elif is_event:
                    self.events[node.name] = class_meta
                elif is_interface:
                    self.interfaces[node.name] = class_meta
                else:
                    # Check if this is a constant/enum class (all Name = Constant assignments)
                    const_variants = {}
                    is_const_class = True
                    for item in node.body:
                        if isinstance(item, ast.Assign) and len(item.targets) == 1:
                            target = item.targets[0]
                            if isinstance(target, ast.Name) and isinstance(item.value, ast.Constant):
                                const_variants[target.id] = item.value.value
                            else:
                                is_const_class = False
                                break
                        else:
                            is_const_class = False
                            break
                    if is_const_class and const_variants:
                        self.const_classes[node.name] = const_variants
                    else:
                        self.structs[node.name] = class_meta

    def parse_state_var(self, node):
        if isinstance(node.target, ast.Name):
            var_name = node.target.id
            var_type = ast.unparse(node.annotation)
            self.state_variables[var_name] = {
                "type": var_type,
                "storage_mode": "instance" # Default
            }

    def parse_function(self, node):
        storage_mode = "instance"
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Attribute) and decorator.value.id == 'state':
                storage_mode = decorator.attr
                
        func_meta = {
            "name": node.name,
            "args": [(arg.arg, ast.unparse(arg.annotation) if arg.annotation else "None") for arg in node.args.args],
            "returns": ast.unparse(node.returns) if node.returns else "None",
            "storage_mode": storage_mode,
            "node": node
        }
        self.functions.append(func_meta)

def parse_source(source_code: str) -> MyceliumCompilerVisitor:
    tree = ast.parse(source_code)
    visitor = MyceliumCompilerVisitor()
    visitor.parse(tree)
    return visitor
