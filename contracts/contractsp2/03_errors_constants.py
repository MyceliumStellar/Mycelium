"""
Test Case 03: Error Handling and Constants.
Tests raise/revert and constant state variables.
"""

MAX_THRESHOLD: constant(uint256) = 5000
MIN_THRESHOLD: constant(uint256) = 100

@external
def validate_value(val: uint256):
    if val < MIN_THRESHOLD:
        raise "Value too low"
    
    if val > MAX_THRESHOLD:
        raise "Value too high"

@external
@view
def get_thresholds() -> (uint256, uint256):
    return (MIN_THRESHOLD, MAX_THRESHOLD)
