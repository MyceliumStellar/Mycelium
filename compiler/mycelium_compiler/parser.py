import ast

class MyceliumCompilerVisitor(ast.NodeVisitor):
    def __init__(self):
        self.contract_name = None
        self.state_variables = {}
        self.functions = []

    def visit_ClassDef(self, node):
        # Enforce that the class marks a contract boundary
        has_decorator = any(d.id == 'contract' for d in node.decorator_list if isinstance(d, ast.Name))
        if not has_decorator:
            raise SyntaxError(f"Class '{node.name}' must be decorated with @contract to be compiled.")
        
        self.contract_name = node.name
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        # Track persistent state structures defined at class-level
        if isinstance(node.target, ast.Name):
            var_name = node.target.id
            var_type = ast.unparse(node.annotation)
            self.state_variables[var_name] = {
                "type": var_type,
                "storage_mode": "instance" # Default
            }

    def visit_FunctionDef(self, node):
        # Extract storage scope decorators
        storage_mode = "instance"
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Attribute) and decorator.value.id == 'state':
                storage_mode = decorator.attr
                
        func_meta = {
            "name": node.name,
            "args": [(arg.arg, ast.unparse(arg.annotation) if arg.annotation else "None") for arg in node.args.args],
            "returns": ast.unparse(node.returns) if node.returns else "None",
            "storage_mode": storage_mode
        }
        self.functions.append(func_meta)

def parse_source(source_code: str) -> MyceliumCompilerVisitor:
    tree = ast.parse(source_code)
    visitor = MyceliumCompilerVisitor()
    visitor.visit(tree)
    return visitor
