"""Governance Executor: executes passed DAO proposals."""
admin: address

@external
def __init__():
    self.admin = msg_sender

@external
def execute(target: address, data: Bytes[1024]):
    assert(msg_sender == self.admin, "Not authorized")
    # Call target with data
    pass
