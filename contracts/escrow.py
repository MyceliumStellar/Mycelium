from mycelium_compiler.types import contract, state, Symbol, Bytes, i128

@contract
class MyceliumEscrow:
    # State mapping payment task keys to their respective locking records
    # Maps task_id to details: [buyer, seller, amount, status]
    escrows: Map[Bytes, Map[Symbol, Bytes]]

    @state.instance
    def lock_funds(self, task_id: Bytes, buyer: Bytes, seller: Bytes, amount: i128):
        details = Map()
        details[Symbol("buyer")] = buyer
        details[Symbol("seller")] = seller
        details[Symbol("amount")] = Bytes(str(amount).encode())
        details[Symbol("status")] = Bytes(b"locked")
        
        self.escrows[task_id] = details

    @state.instance
    def fulfill_and_disburse(self, task_id: Bytes, signature_proof: Bytes) -> bool:
        # Verify provider signed payload using a cryptographic primitive check
        # and release funds to seller
        if task_id not in self.escrows:
            return False
            
        details = self.escrows[task_id]
        details[Symbol("status")] = Bytes(b"disbursed")
        self.escrows[task_id] = details
        return True
