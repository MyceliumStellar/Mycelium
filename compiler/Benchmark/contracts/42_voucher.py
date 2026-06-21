"""Voucher: claimable tokens with a unique hash."""
token: address
vouchers: Mapping[bytes32, bool]
voucher_values: Mapping[bytes32, uint256]

@external
def __init__(token_addr: address):
    self.token = token_addr

@external
def create_voucher(v_hash: bytes32, value: uint256):
    self.vouchers[v_hash] = True
    self.voucher_values[v_hash] = value

@external
def claim_voucher(secret: bytes32):
    # Logic to verify hash and transfer tokens
    pass
