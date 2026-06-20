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

# Capitalized Types
class Address(str):
    def require_auth(self):
        pass

class U128(int):
    pass

class U64(int):
    pass

class U32(int):
    pass

class I128(int):
    pass

class I32(int):
    pass

class Bool:
    pass

class StorageMock:
    def get(self, key, default=None):
        return default
    def set(self, key, value):
        pass
    def has(self, key):
        return False
    def remove(self, key):
        pass

class Env:
    def storage(self):
        return StorageMock()
    def ledger(self):
        return self
    def timestamp(self):
        return 0
    def sequence(self):
        return 0
    def current_contract_address(self):
        return Address("")
    def current_contract(self):
        return Address("")
    def call(self, contract, method, args):
        pass
    def invoke_contract(self, contract, method, args):
        pass
    def transfer(self, from_addr, to_addr, token, amount):
        pass
    def emit_event(self, topic, data):
        pass
    def crypto(self):
        return self
    def sha256(self, data):
        return Bytes(b"")
    def keccak256(self, data):
        return Bytes(b"")
    def verify_sig_ed25519(self, pk, msg, sig):
        return True

# Decorators
def contract(cls):
    return cls

def external(func):
    return func

def view(func):
    return func

def storage(func):
    return func

def event(cls):
    return cls

def auth(func):
    return func

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
