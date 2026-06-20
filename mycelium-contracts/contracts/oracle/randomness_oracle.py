"""
Randomness Oracle — Commit-reveal verifiable randomness feed with subscription fee and callbacks.

Mycelium Smart Contract for Stellar
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    INSUFFICIENT_FUNDS = 4
    REQUEST_NOT_FOUND = 5
    ALREADY_FULFILLED = 6
    INVALID_PROOF = 7
    INVALID_CALLBACK = 8
    NO_COMMITMENT_LEFT = 9
    TRANSFER_FAILED = 10
    REENTRANT_CALL = 11


class RequestStatus:
    PENDING = 0
    FULFILLED = 1
    FAILED_CALLBACK = 2
    REFUNDED = 3


@contract
class RandomnessOracle:
    """Verifiable randomness oracle implementing a commit-reveal hash-chain scheme,
    managing subscriber fee balances, and executing consumer contract callbacks."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        fee_token: Address,
        fee_per_request: U128,
        operator: Address,
        initial_commitment: Bytes,
    ):
        """Initialize the oracle contract configurations."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("fee_token", fee_token)
        self.storage.set("fee_per_request", fee_per_request)
        self.storage.set("operator", operator)
        self.storage.set("current_commitment", initial_commitment)
        
        self.storage.set("request_count", U64(0))
        self.storage.set("withdrawable_fees", U128(0))
        self.storage.set("execution_lock", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "operator": operator,
            "fee_token": fee_token,
            "fee_per_request": fee_per_request,
        })

    # ------------------------------------------------------------------ #
    #  Subscription Management                                             #
    # ------------------------------------------------------------------ #

    @external
    def deposit_subscription(self, subscriber: Address, amount: U128):
        """Deposit funds to a subscriber balance.

        Args:
            subscriber: The address whose subscription is funded.
            amount: The token amount to deposit.
        """
        self._require_initialized()
        subscriber.require_auth()

        fee_token = self.storage.get("fee_token")
        contract_addr = self.env.current_contract_address()
        success = self.env.invoke_contract(fee_token, "transfer", [subscriber, contract_addr, amount])
        if not success:
            raise ContractError.TRANSFER_FAILED

        balance = self.storage.get(("subscription", subscriber), U128(0))
        self.storage.set(("subscription", subscriber), balance + amount)

        self.env.emit_event("subscription_funded", {
            "subscriber": subscriber,
            "amount": amount,
            "new_balance": balance + amount,
        })

    @external
    def withdraw_subscription(self, subscriber: Address, amount: U128):
        """Withdraw unused subscription funds.

        Args:
            subscriber: The subscriber address.
            amount: The amount to withdraw.
        """
        self._require_initialized()
        subscriber.require_auth()

        balance = self.storage.get(("subscription", subscriber), U128(0))
        if balance < amount:
            raise ContractError.INSUFFICIENT_FUNDS

        self.storage.set(("subscription", subscriber), balance - amount)

        fee_token = self.storage.get("fee_token")
        contract_addr = self.env.current_contract_address()
        success = self.env.invoke_contract(fee_token, "transfer", [contract_addr, subscriber, amount])
        if not success:
            raise ContractError.TRANSFER_FAILED

        self.env.emit_event("subscription_withdrawn", {
            "subscriber": subscriber,
            "amount": amount,
            "new_balance": balance - amount,
        })

    # ------------------------------------------------------------------ #
    #  Randomness Request                                                 #
    # ------------------------------------------------------------------ #

    @external
    def request_randomness(
        self,
        caller: Address,
        callback_contract: Address,
        callback_method: Symbol,
        seed: Bytes,
    ) -> U64:
        """Submit a request for verifiable randomness.

        Args:
            caller: Address requesting the randomness (charged the fee).
            callback_contract: The contract address to invoke when fulfilled.
            callback_method: Symbol representing the callback function name.
            seed: Client-side seed to mix with the oracle's entropy.
        """
        self._require_initialized()
        caller.require_auth()

        # Charge fee
        fee = self.storage.get("fee_per_request")
        sub_balance = self.storage.get(("subscription", caller), U128(0))
        if sub_balance < fee:
            raise ContractError.INSUFFICIENT_FUNDS

        self.storage.set(("subscription", caller), sub_balance - fee)
        
        # Accumulate fees for operator
        withdrawable = self.storage.get("withdrawable_fees")
        self.storage.set("withdrawable_fees", withdrawable + fee)

        request_id = self.storage.get("request_count") + U64(1)
        self.storage.set("request_count", request_id)

        now = self.env.ledger().timestamp()

        request = {
            "id": request_id,
            "caller": caller,
            "callback_contract": callback_contract,
            "callback_method": callback_method,
            "seed": seed,
            "status": RequestStatus.PENDING,
            "requested_at": now,
            "random_value": Bytes(b""),
        }

        self.storage.set(("request", request_id), request)

        self.env.emit_event("randomness_requested", {
            "request_id": request_id,
            "caller": caller,
            "callback_contract": callback_contract,
            "requested_at": now,
        })

        return request_id

    # ------------------------------------------------------------------ #
    #  Fulfillment                                                        #
    # ------------------------------------------------------------------ #

    @external
    def fulfill_randomness(self, operator: Address, request_id: U64, reveal_preimage: Bytes):
        """Provide preimage to fulfill the randomness request and trigger subscriber callback.

        Args:
            operator: Authorized operator address.
            request_id: Request ID to fulfill.
            reveal_preimage: Preimage of the hash chain, proving valid reveal.
        """
        self._require_initialized()
        operator.require_auth()
        self._require_operator(operator)
        self._require_no_reentrant()

        request = self.storage.get(("request", request_id), None)
        if request is None:
            raise ContractError.REQUEST_NOT_FOUND
        if request["status"] != RequestStatus.PENDING:
            raise ContractError.ALREADY_FULFILLED

        # Verify hash-chain preimage: keccak256(reveal_preimage) == current_commitment
        current_commitment = self.storage.get("current_commitment")
        # Compute hash
        computed_hash = self.env.crypto().keccak256(reveal_preimage)
        if computed_hash != current_commitment:
            raise ContractError.INVALID_PROOF

        # Move up/down hash chain: the preimage becomes the new commitment for the next step
        self.storage.set("current_commitment", reveal_preimage)

        # Mix preimage + subscriber seed to generate the final pseudorandom value
        randomness = self.env.crypto().keccak256(reveal_preimage, request["seed"])

        # Attempt callback invocation
        request["random_value"] = randomness
        
        callback_contract = request["callback_contract"]
        callback_method = request["callback_method"]

        self.storage.set("execution_lock", True)
        
        # Invoke consumer callback
        try:
            # signature: callback(request_id, randomness_bytes)
            success = self.env.invoke_contract(callback_contract, callback_method, [request_id, randomness])
            if success:
                request["status"] = RequestStatus.FULFILLED
            else:
                request["status"] = RequestStatus.FAILED_CALLBACK
        except Exception:
            request["status"] = RequestStatus.FAILED_CALLBACK

        self.storage.set(("request", request_id), request)
        self.storage.set("execution_lock", False)

        self.env.emit_event("randomness_fulfilled", {
            "request_id": request_id,
            "randomness": randomness,
            "status": request["status"],
        })

    # ------------------------------------------------------------------ #
    #  Admin & Operator Configuration                                      #
    # ------------------------------------------------------------------ #

    @external
    def update_fee(self, admin: Address, new_fee: U128):
        """Update request fee. Only Admin."""
        self._require_admin(admin)
        self.storage.set("fee_per_request", new_fee)
        self.env.emit_event("fee_updated", {"new_fee": new_fee})

    @external
    def set_operator(self, admin: Address, operator: Address, commitment: Bytes):
        """Set new operator and reset the hash chain commitment. Only Admin."""
        self._require_admin(admin)
        self.storage.set("operator", operator)
        self.storage.set("current_commitment", commitment)
        self.env.emit_event("operator_updated", {"operator": operator, "commitment": commitment})

    @external
    def withdraw_fees(self, operator: Address, recipient: Address, amount: U128):
        """Withdraw accumulated operator service fees. Only Operator."""
        self._require_initialized()
        operator.require_auth()
        self._require_operator(operator)

        withdrawable = self.storage.get("withdrawable_fees", U128(0))
        if withdrawable < amount:
            raise ContractError.INSUFFICIENT_FUNDS

        self.storage.set("withdrawable_fees", withdrawable - amount)

        fee_token = self.storage.get("fee_token")
        contract_addr = self.env.current_contract_address()
        success = self.env.invoke_contract(fee_token, "transfer", [contract_addr, recipient, amount])
        if not success:
            raise ContractError.TRANSFER_FAILED

        self.env.emit_event("fees_withdrawn", {"recipient": recipient, "amount": amount})

    @external
    def refund_request(self, admin: Address, request_id: U64):
        """Refund a pending request in case of operator timeout. Only Admin."""
        self._require_admin(admin)
        request = self.storage.get(("request", request_id), None)
        if request is None:
            raise ContractError.REQUEST_NOT_FOUND
        if request["status"] != RequestStatus.PENDING:
            raise ContractError.ALREADY_FULFILLED

        request["status"] = RequestStatus.REFUNDED
        self.storage.set(("request", request_id), request)

        fee = self.storage.get("fee_per_request")
        caller = request["caller"]
        sub_balance = self.storage.get(("subscription", caller), U128(0))
        self.storage.set(("subscription", caller), sub_balance + fee)

        withdrawable = self.storage.get("withdrawable_fees", U128(0))
        if withdrawable >= fee:
            self.storage.set("withdrawable_fees", withdrawable - fee)

        self.env.emit_event("request_refunded", {"request_id": request_id, "caller": caller})

    @external
    def transfer_admin(self, admin: Address, new_admin: Address):
        """Transfer admin role. Only Admin."""
        self._require_admin(admin)
        self.storage.set("admin", new_admin)
        self.env.emit_event("admin_transferred", {"old_admin": admin, "new_admin": new_admin})

    # ------------------------------------------------------------------ #
    #  View Functions                                                     #
    # ------------------------------------------------------------------ #

    @view
    def get_subscription_balance(self, subscriber: Address) -> U128:
        """Get current subscription balance of an address."""
        self._require_initialized()
        return self.storage.get(("subscription", subscriber), U128(0))

    @view
    def get_request(self, request_id: U64) -> Map:
        """Get details of a randomness request."""
        self._require_initialized()
        request = self.storage.get(("request", request_id), None)
        if request is None:
            raise ContractError.REQUEST_NOT_FOUND
        return request

    @view
    def get_commitment(self) -> Bytes:
        """Get the active commitment of the operator hash-chain."""
        self._require_initialized()
        return self.storage.get("current_commitment")

    @view
    def get_config(self) -> Map:
        """Get configuration details."""
        return {
            "admin": self.storage.get("admin"),
            "operator": self.storage.get("operator"),
            "fee_token": self.storage.get("fee_token"),
            "fee_per_request": self.storage.get("fee_per_request"),
            "withdrawable_fees": self.storage.get("withdrawable_fees"),
            "request_count": self.storage.get("request_count"),
        }

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                   #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        caller.require_auth()
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_operator(self, caller: Address):
        operator = self.storage.get("operator")
        if caller != operator:
            raise ContractError.UNAUTHORIZED

    def _require_no_reentrant(self):
        if self.storage.get("execution_lock", False):
            raise ContractError.REENTRANT_CALL
