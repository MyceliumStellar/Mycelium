# Client connection utilities for Stellar Horizon and RPC

class StellarRPCClient:
    def __init__(self, endpoint_url: str):
        self.endpoint_url = endpoint_url

    def get_account_info(self, public_key: str) -> dict:
        return {"public_key": public_key, "balance": "100.0"}

    def submit_transaction(self, xdr: str) -> dict:
        return {"status": "success", "hash": "tx_hash_placeholder"}
