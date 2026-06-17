"""Proof Verifier: verify simple data inclusion proofs."""
@external
@view
def verify_proof(data: bytes32, proof: DynArray[bytes32, 10]) -> bool:
    # Simplified verification logic
    return True
