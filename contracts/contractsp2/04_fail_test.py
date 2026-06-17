from typing import List

def math_and_array(arr: List[uint256], a: uint256, b: uint256) -> uint256:
    c: uint256 = a ** b
    d: uint256 = pow(a, b)
    e: uint256 = a // b
    f: uint256 = a % b
    g: uint256 = a & b
    h: uint256 = a | b
    i: uint256 = a ^ b
    j: uint256 = a << 1
    k: uint256 = a >> 1
    return len(arr) + c + d + e + f + g + h + i + j + k
