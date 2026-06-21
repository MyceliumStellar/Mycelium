"""
Stealth Address Registry — Stealth key registrations, transfer announcements, and signature-verified withdrawals.

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
    STEALTH_ADDRESS_EXISTS = 5
    STEALTH_ADDRESS_NOT_FOUND = 6
    ALREADY_CLAIMED = 7
    INVALID_SIGNATURE = 8
    KEYS_NOT_REGISTERED = 9


@contract
class StealthAddressSystem:
    """Manages stealth recipient key registries, transfer announcements, and signature-verified claims."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, token: Address):
        """Initialize the Stealth Address System."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("token", token)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {"admin": admin, "token": token})

    # ------------------------------------------------------------------ #
    #  Registry Operations                                               #
    # ------------------------------------------------------------------ #

    @external
    def register_keys(self, user: Address, spend_pubkey: Bytes, view_pubkey: Bytes):
        """Register public keys for stealth address generation."""
        self._require_initialized()
        user.require_auth()

        keys = {
            "spend_pubkey": spend_pubkey,
            "view_pubkey": view_pubkey
        }

        self.storage.set(("keys", user), keys)
        self.env.emit_event("keys_registered", {
            "user": user,
            "spend_pubkey": spend_pubkey,
            "view_pubkey": view_pubkey
        })

    # ------------------------------------------------------------------ #
    #  Stealth Transfers                                                 #
    # ------------------------------------------------------------------ #

    @external
    def announce_transfer(
        self,
        sender: Address,
        stealth_address: Address,
        ephemeral_pubkey: Bytes,
        amount: U128
    ) -> Bool:
        """Lock tokens and announce a stealth transfer to the one-time stealth address."""
        self._require_initialized()
        sender.require_auth()

        # Check if stealth address already has a pending transfer
        if self.storage.get(("transfer", stealth_address), None) is not None:
            raise ContractError.STEALTH_ADDRESS_EXISTS

        token = self.storage.get("token")
        contract_addr = self.env.current_contract_address()

        # Transfer tokens from sender to contract
        success = self.env.invoke_contract(token, "transfer", [sender, contract_addr, amount])
        if not success:
            raise ContractError.TRANSFER_FAILED

        # Record transfer parameters
        transfer_details = {
            "sender": sender,
            "ephemeral_pubkey": ephemeral_pubkey,
            "amount": amount,
            "claimed": False
        }

        self.storage.set(("transfer", stealth_address), transfer_details)

        self.env.emit_event("transfer_announced", {
            "sender": sender,
            "stealth_address": stealth_address,
            "ephemeral_pubkey": ephemeral_pubkey,
            "amount": amount
        })

        return True

    @external
    def claim_transfer(
        self,
        stealth_address: Address,
        recipient: Address,
        signature: Bytes
    ) -> Bool:
        """Claim a stealth transfer by providing a signature from the stealth address private key.

        The claimant provides a signature of the recipient address signed by the stealth private key,
        verifying they hold the private key associated with the stealth public key.
        """
        self._require_initialized()
        
        # We do not call recipient.require_auth() here so that claim can be triggered by a relayer/proxy,
        # preserving the link privacy of the recipient.

        transfer = self.storage.get(("transfer", stealth_address), None)
        if transfer is None:
            raise ContractError.STEALTH_ADDRESS_NOT_FOUND

        if transfer["claimed"]:
            raise ContractError.ALREADY_CLAIMED

        amount = transfer["amount"]

        # Signature verification:
        # The message signed is the recipient Address.
        # We verify using the stealth_address (which acts as the one-time public key)
        # Note: the stealth_address is an Address, which has a public key representation.
        # We assume standard signature check against the stealth_address public key
        # In Stellar, we can use env.crypto().verify_sig(stealth_address, recipient, signature)
        # We check signature:
        message = self.env.crypto().keccak256(recipient)
        
        # Verify the signature matches the stealth_address.
        # Ed25519 signature verification:
        # verify_sig_ed25519(public_key, message, signature)
        # We parse the public key bytes from the stealth_address or use it directly
        stealth_pubkey = stealth_address.to_bytes()
        valid = self.env.crypto().verify_sig_ed25519(stealth_pubkey, message, signature)
        if not valid:
            raise ContractError.INVALID_SIGNATURE

        # Mark as claimed
        transfer["claimed"] = True
        self.storage.set(("transfer", stealth_address), transfer)

        # Distribute tokens to recipient
        token = self.storage.get("token")
        contract_addr = self.env.current_contract_address()
        success = self.env.invoke_contract(token, "transfer", [contract_addr, recipient, amount])
        if not success:
            raise ContractError.TRANSFER_FAILED

        self.env.emit_event("transfer_claimed", {
            "stealth_address": stealth_address,
            "recipient": recipient,
            "amount": amount
        })

        return True

    # ------------------------------------------------------------------ #
    #  View Operations                                                    #
    # ------------------------------------------------------------------ #

    @view
    def get_keys(self, user: Address) -> Map:
        """Get the public keys of a registered user."""
        self._require_initialized()
        keys = self.storage.get(("keys", user), None)
        if keys is None:
            raise ContractError.KEYS_NOT_REGISTERED
        return keys

    @view
    def get_transfer_details(self, stealth_address: Address) -> Map:
        """Retrieve details of a stealth transfer announcement."""
        self._require_initialized()
        transfer = self.storage.get(("transfer", stealth_address), None)
        if transfer is None:
            raise ContractError.STEALTH_ADDRESS_NOT_FOUND
        
        res = Map()
        res.set(Symbol("sender"), transfer["sender"])
        res.set(Symbol("ephemeral_pubkey"), transfer["ephemeral_pubkey"])
        res.set(Symbol("amount"), transfer["amount"])
        res.set(Symbol("claimed"), transfer["claimed"])
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                   #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED
