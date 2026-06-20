"""
Privacy Mixer — Tornado-style token mixer, commitment registry, nullifier spend checks, and relayer fee splits.

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
    TRANSFER_FAILED = 4
    COMMITMENT_EXISTS = 5
    COMMITMENT_NOT_FOUND = 6
    NULLIFIER_ALREADY_SPENT = 7
    INVALID_WITHDRAW_PROOF = 8
    INVALID_RELAYER_FEE = 9


@contract
class PrivacyMixer:
    """Manages anonymous token deposits and withdrawals using commitments, nullifiers, and relayer incentives."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        mix_token: Address,
        deposit_amount: U128,
        relayer_fee: U128
    ):
        """Initialize the mixer with token specifications, size limits, and fee details."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        if relayer_fee >= deposit_amount:
            raise ContractError.INVALID_RELAYER_FEE

        self.storage.set("admin", admin)
        self.storage.set("mix_token", mix_token)
        self.storage.set("deposit_amount", deposit_amount)
        self.storage.set("relayer_fee", relayer_fee)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "mix_token": mix_token,
            "deposit_amount": deposit_amount,
            "relayer_fee": relayer_fee
        })

    # ------------------------------------------------------------------ #
    #  Admin Operations                                                   #
    # ------------------------------------------------------------------ #

    @external
    def update_mixer_configs(self, admin: Address, new_amount: U128, new_fee: U128):
        """Configure deposit sizing and relayer splits. Only Admin."""
        self._require_admin(admin)
        if new_fee >= new_amount:
            raise ContractError.INVALID_RELAYER_FEE

        self.storage.set("deposit_amount", new_amount)
        self.storage.set("relayer_fee", new_fee)

        self.env.emit_event("configs_updated", {"deposit_amount": new_amount, "relayer_fee": new_fee})

    # ------------------------------------------------------------------ #
    #  User Operations                                                    #
    # ------------------------------------------------------------------ #

    @external
    def deposit(self, depositor: Address, commitment: Bytes):
        """Deposit tokens and register the commitment hash. C = Keccak256(nullifier + secret)."""
        self._require_initialized()
        depositor.require_auth()

        # Check if commitment is already registered
        if self.storage.get(("commitment", commitment), False):
            raise ContractError.COMMITMENT_EXISTS

        # Charge deposit amount
        deposit_amount = self.storage.get("deposit_amount")
        mix_token = self.storage.get("mix_token")
        contract_addr = self.env.current_contract_address()

        success = self.env.invoke_contract(mix_token, "transfer", [depositor, contract_addr, deposit_amount])
        if not success:
            raise ContractError.TRANSFER_FAILED

        # Save commitment
        self.storage.set(("commitment", commitment), True)

        self.env.emit_event("deposited", {
            "commitment": commitment,
            "amount": deposit_amount
        })

    @external
    def withdraw(
        self,
        relayer: Address,
        recipient: Address,
        nullifier: Bytes,
        secret: Bytes,
        commitment: Bytes
    ) -> Bool:
        """Withdraw tokens by proving knowledge of commitment secret. Distributes fee to relayer."""
        self._require_initialized()
        relayer.require_auth() # The relayer calls this to hide the recipient's origin address on chain

        # Check if nullifier has been spent
        if self.storage.get(("nullifier", nullifier), False):
            raise ContractError.NULLIFIER_ALREADY_SPENT

        # Check if commitment is active in registry
        if not self.storage.get(("commitment", commitment), False):
            raise ContractError.COMMITMENT_NOT_FOUND

        # Cryptographic verification: Keccak256(nullifier + secret) == commitment
        expected_commitment = self.env.crypto().keccak256(nullifier, secret)
        if expected_commitment != commitment:
            raise ContractError.INVALID_WITHDRAW_PROOF

        # Mark nullifier as spent and deactivate commitment
        self.storage.set(("nullifier", nullifier), True)
        self.storage.set(("commitment", commitment), False)

        # Distribute tokens
        deposit_amount = self.storage.get("deposit_amount")
        relayer_fee = self.storage.get("relayer_fee")
        payout_amount = deposit_amount - relayer_fee

        mix_token = self.storage.get("mix_token")
        contract_addr = self.env.current_contract_address()

        # Transfer payout to recipient
        success1 = self.env.invoke_contract(mix_token, "transfer", [contract_addr, recipient, payout_amount])
        if not success1:
            raise ContractError.TRANSFER_FAILED

        # Transfer fee to relayer
        if relayer_fee > U128(0):
            success2 = self.env.invoke_contract(mix_token, "transfer", [contract_addr, relayer, relayer_fee])
            if not success2:
                raise ContractError.TRANSFER_FAILED

        self.env.emit_event("withdrawn", {
            "recipient": recipient,
            "relayer": relayer,
            "payout": payout_amount,
            "fee": relayer_fee
        })

        return True

    # ------------------------------------------------------------------ #
    #  View Operations                                                    #
    # ------------------------------------------------------------------ #

    @view
    def is_commitment_active(self, commitment: Bytes) -> Bool:
        """Check if a commitment hash is valid and unspent."""
        self._require_initialized()
        return self.storage.get(("commitment", commitment), False)

    @view
    def is_nullifier_spent(self, nullifier: Bytes) -> Bool:
        """Check if a nullifier has been spent."""
        self._require_initialized()
        return self.storage.get(("nullifier", nullifier), False)

    @view
    def get_mixer_details(self) -> Map:
        """Get global status details of the mixer."""
        self._require_initialized()
        res = Map()
        res.set(Symbol("deposit_amount"), self.storage.get("deposit_amount"))
        res.set(Symbol("relayer_fee"), self.storage.get("relayer_fee"))
        res.set(Symbol("mix_token"), self.storage.get("mix_token"))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                   #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        caller.require_auth()
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED
