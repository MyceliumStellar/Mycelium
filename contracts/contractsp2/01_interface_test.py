"""
Test Case 01: External Interface Interaction.
This contract defines an interface and calls an external contract.
"""

@interface
class IERC20:
    def transfer(to: address, amount: uint256) -> bool:
        pass
    
    @view
    def balance_of(account: address) -> uint256:
        pass

@external
def sweep(token_addr: address, amount: uint256):
    # Calling external contract via interface
    token: IERC20 = IERC20(token_addr)
    token.transfer(msg_sender, amount)

@external
@view
def get_remote_balance(token_addr: address, account: address) -> uint256:
    return IERC20(token_addr).balance_of(account)
