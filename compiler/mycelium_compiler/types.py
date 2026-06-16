# Custom types to be imported by mycelium smart contracts for static typing and compiler awareness

class Symbol(str):
    pass

class i128(int):
    pass

class i64(int):
    pass

class i32(int):
    pass

class u64(int):
    pass

class u32(int):
    pass

# Decorator placeholders for editor validation and runtime execution
def contract(cls):
    return cls

class state:
    @staticmethod
    def instance(func):
        return func
        
    @staticmethod
    def persistent(func):
        return func
        
    @staticmethod
    def temporary(func):
        return func
