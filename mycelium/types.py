# Core Mycelium Type System for Compiler validation and AST Type Enforcement

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

# Mock collections for type validator checks
class Map(dict):
    pass

class Vec(list):
    pass

class Bytes(bytes):
    pass

# Decorators
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
