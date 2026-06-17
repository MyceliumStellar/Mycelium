"""Flash Loan: provide uncollateralized loans for one transaction."""
owner: address
fee_percent: uint256

@event
class FlashLoan:
    target: indexed(address)
    amount: uint256
    fee: uint256

@external
def __init__(fee: uint256):
    self.owner = msg_sender
    self.fee_percent = fee

@external
def execute_flash_loan(target: address, amount: uint256):
    fee: uint256 = (amount * self.fee_percent) / 10000
    # Logic to transfer tokens, call target, and verify repayment
    self.FlashLoan(target, amount, fee)

@external
@view
def get_fee(amount: uint256) -> uint256:
    return (amount * self.fee_percent) / 10000
