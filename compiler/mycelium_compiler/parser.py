import ast

class MyceliumCompilerVisitor(ast.NodeVisitor):
    def __init__(self):
        self.contract_name = None
        self.state_variables = {}
        self.functions = []
        self.events = {}
        self.interfaces = {}
        self.structs = {}
        self.class_mode = False

    def parse(self, tree):
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
                
                if is_event:
                    self.events[node.name] = class_meta
                elif is_interface:
                    self.interfaces[node.name] = class_meta
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
