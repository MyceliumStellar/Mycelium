"""
Fractional NFT — NFT locking, fractionalization, buyout voting, and settlement.

Mycelium Smart Contract for Stellar. Locks an NFT, mints fractional ERC20-like
tokens, handles fraction transfers, allows buyout proposals, hosts holder voting,
and distributes buyout funds proportionally upon successful redemption.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)

class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    PAUSED = 4
    ALREADY_FRACTIONALIZED = 5
    NOT_FRACTIONALIZED = 6
    BUYOUT_ACTIVE = 7
    NO_BUYOUT_ACTIVE = 8
    BUYOUT_VOTING_OPEN = 9
    BUYOUT_VOTING_CLOSED = 10
    VOTING_NOT_ENDED = 11
    VOTE_ALREADY_CAST = 12
    INSUFFICIENT_BALANCE = 13
    INVALID_AMOUNT = 14
    BUYOUT_NOT_COMPLETED = 15

@contract
class FractionalNFT:
    """
    Manages fractional ownership of a single locked NFT.
    Supports trading fractions, proposing a reserve buyout, voting on the buyout,
    and burning fractions for buyout funds.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        nft_contract: Address,
        token_id: U64,
        payment_token: Address,
        total_fractions: U128
    ):
        """Initialize the fractional vault parameters."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("nft_contract", nft_contract)
        self.storage.set("token_id", token_id)
        self.storage.set("payment_token", payment_token)
        self.storage.set("total_fractions", total_fractions)
        self.storage.set("fractionalized", False)
        self.storage.set("buyout_active", False)
        self.storage.set("buyout_completed", False)
        self.storage.set("paused", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "nft_contract": nft_contract,
            "token_id": token_id,
            "total_fractions": total_fractions
        })

    @external
    def set_paused(self, caller: Address, paused: Bool):
        """Pause trading and buyout actions."""
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        self.storage.set("paused", paused)
        self.env.emit_event("paused_status", {"paused": paused})

    @external
    def lock_and_fractionalize(self, caller: Address):
        """
        Locks the target NFT in the vault and mints the entire fraction supply to the caller.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if self.storage.get("fractionalized", False):
            raise ContractError.ALREADY_FRACTIONALIZED

        nft_contract = self.storage.get("nft_contract")
        token_id = self.storage.get("token_id", U64(0))

        # Escrow the NFT in this vault contract
        self.env.call(nft_contract, "transfer", caller, self.env.current_contract_address(), token_id)

        # Distribute fractions total supply to the fractionalizer
        total_fractions = self.storage.get("total_fractions", U128(0))
        self.storage.set(f"bal_{caller}", total_fractions)
        self.storage.set("fractionalized", True)

        self.env.emit_event("fractionalized", {
            "owner": caller,
            "total_fractions": total_fractions
        })

    # --- ERC20 FRACTION METHODS ---

    @external
    def transfer(self, caller: Address, to: Address, amount: U128):
        """Transfer fractions to another wallet."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if amount == U128(0):
            raise ContractError.INVALID_AMOUNT

        sender_bal = self.storage.get(f"bal_{caller}", U128(0))
        if sender_bal < amount:
            raise ContractError.INSUFFICIENT_BALANCE

        self.storage.set(f"bal_{caller}", sender_bal - amount)
        recipient_bal = self.storage.get(f"bal_{to}", U128(0))
        self.storage.set(f"bal_{to}", recipient_bal + amount)

        self.env.emit_event("transfer", {
            "from": caller,
            "to": to,
            "amount": amount
        })

    @external
    def approve(self, caller: Address, spender: Address, amount: U128):
        """Approve spender to transfer fractions."""
        caller.require_auth()
        self._require_initialized()

        self.storage.set(f"allow_{caller}_{spender}", amount)
        self.env.emit_event("approval", {
            "owner": caller,
            "spender": spender,
            "amount": amount
        })

    @external
    def transfer_from(self, caller: Address, from_addr: Address, to_addr: Address, amount: U128):
        """Transfer fractions on behalf of a holder."""
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        allowance = self.storage.get(f"allow_{from_addr}_{caller}", U128(0))
        if allowance < amount:
            raise ContractError.UNAUTHORIZED

        from_bal = self.storage.get(f"bal_{from_addr}", U128(0))
        if from_bal < amount:
            raise ContractError.INSUFFICIENT_BALANCE

        # Update allowances & balances
        self.storage.set(f"allow_{from_addr}_{caller}", allowance - amount)
        self.storage.set(f"bal_{from_addr}", from_bal - amount)
        to_bal = self.storage.get(f"bal_{to_addr}", U128(0))
        self.storage.set(f"bal_{to_addr}", to_bal + amount)

        self.env.emit_event("transfer", {
            "from": from_addr,
            "to": to_addr,
            "amount": amount
        })

    # --- RESERVE BUYOUT SYSTEM ---

    @external
    def propose_buyout(self, caller: Address, price: U128, voting_duration: U64):
        """
        Propose a reserve buyout of the NFT.
        Locks the total payment price inside the contract until voting finishes.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_not_paused()

        if not self.storage.get("fractionalized", False):
            raise ContractError.NOT_FRACTIONALIZED
        if self.storage.get("buyout_active", False):
            raise ContractError.BUYOUT_ACTIVE
        if self.storage.get("buyout_completed", False):
            raise ContractError.BUYOUT_ACTIVE

        if price == U128(0):
            raise ContractError.INVALID_AMOUNT
        if voting_duration < U64(3600):  # Min 1 hour voting
            raise ContractError.INVALID_AMOUNT

        # Collect full payment from buyout proposer
        payment_token = self.storage.get("payment_token")
        self.env.call(payment_token, "transfer", caller, self.env.current_contract_address(), price)

        # Setup buyout
        self.storage.set("buyout_active", True)
        self.storage.set("buyout_proposer", caller)
        self.storage.set("buyout_price", price)
        self.storage.set("buyout_end", self._get_now() + voting_duration)
        self.storage.set("buyout_votes_yes", U128(0))
        self.storage.set("buyout_votes_no", U128(0))

        self.env.emit_event("buyout_proposed", {
            "proposer": caller,
            "price": price,
            "expiry": self._get_now() + voting_duration
        })

    @external
    def vote_on_buyout(self, caller: Address, support: Bool):
        """Cast fraction weight vote on active buyout proposal."""
        caller.require_auth()
        self._require_initialized()

        if not self.storage.get("buyout_active", False):
            raise ContractError.NO_BUYOUT_ACTIVE

        now = self._get_now()
        end = self.storage.get("buyout_end", U64(0))
        if now >= end:
            raise ContractError.BUYOUT_VOTING_CLOSED

        if self.storage.get(f"voted_{caller}", False):
            raise ContractError.VOTE_ALREADY_CAST

        weight = self.storage.get(f"bal_{caller}", U128(0))
        if weight == U128(0):
            raise ContractError.INSUFFICIENT_BALANCE

        self.storage.set(f"voted_{caller}", True)
        # Store vote choices
        if support:
            yes_votes = self.storage.get("buyout_votes_yes", U128(0)) + weight
            self.storage.set("buyout_votes_yes", yes_votes)
        else:
            no_votes = self.storage.get("buyout_votes_no", U128(0)) + weight
            self.storage.set("buyout_votes_no", no_votes)

        self.env.emit_event("vote_cast", {
            "voter": caller,
            "support": support,
            "weight": weight
        })

    @external
    def end_buyout(self, caller: Address):
        """Evaluate voting result. If yes > 50%, unlock NFT and open claims."""
        self._require_initialized()

        if not self.storage.get("buyout_active", False):
            raise ContractError.NO_BUYOUT_ACTIVE

        now = self._get_now()
        end = self.storage.get("buyout_end", U64(0))
        if now < end:
            raise ContractError.VOTING_NOT_ENDED

        proposer = self.storage.get("buyout_proposer")
        price = self.storage.get("buyout_price", U128(0))
        yes_votes = self.storage.get("buyout_votes_yes", U128(0))
        no_votes = self.storage.get("buyout_votes_no", U128(0))
        total_fractions = self.storage.get("total_fractions", U128(0))

        # Check threshold (yes votes must exceed 50% of total fractions)
        threshold = total_fractions / U128(2)

        self.storage.set("buyout_active", False)

        if yes_votes > threshold:
            # BUYOUT APPROVED
            self.storage.set("buyout_completed", True)
            self.storage.set("buyout_claim_funds", price)

            # Transfer NFT to proposer
            nft_contract = self.storage.get("nft_contract")
            token_id = self.storage.get("token_id", U64(0))
            self.env.call(nft_contract, "transfer", self.env.current_contract_address(), proposer, token_id)

            self.env.emit_event("buyout_settled", {
                "proposer": proposer,
                "price": price,
                "yes_votes": yes_votes
            })
        else:
            # BUYOUT REJECTED - Refund proposer
            payment_token = self.storage.get("payment_token")
            self.env.call(payment_token, "transfer", self.env.current_contract_address(), proposer, price)

            # Reset buyout parameters
            self.storage.remove("buyout_proposer")
            self.storage.remove("buyout_price")
            self.storage.remove("buyout_end")
            self.storage.remove("buyout_votes_yes")
            self.storage.remove("buyout_votes_no")

            self.env.emit_event("buyout_rejected", {
                "proposer": proposer,
                "yes_votes": yes_votes,
                "no_votes": no_votes
            })

    @external
    def claim_funds(self, caller: Address):
        """
        Burn caller's fractions in exchange for their pro-rata share of buyout funds.
        Can only be executed after buyout is successfully completed.
        """
        caller.require_auth()
        self._require_initialized()

        if not self.storage.get("buyout_completed", False):
            raise ContractError.BUYOUT_NOT_COMPLETED

        amount = self.storage.get(f"bal_{caller}", U128(0))
        if amount == U128(0):
            raise ContractError.INSUFFICIENT_BALANCE

        claim_funds = self.storage.get("buyout_claim_funds", U128(0))
        total_fractions = self.storage.get("total_fractions", U128(0))

        # Pro-rata calculate: (caller_fractions * total_funds) / total_fractions
        share = (amount * claim_funds) / total_fractions

        # Burn caller's fractions
        self.storage.set(f"bal_{caller}", U128(0))

        # Transfer pro-rata payment to caller
        payment_token = self.storage.get("payment_token")
        self.env.call(payment_token, "transfer", self.env.current_contract_address(), caller, share)

        self.env.emit_event("fractions_redeemed", {
            "caller": caller,
            "fractions_burned": amount,
            "funds_claimed": share
        })

    # --- VIEWS ---

    @view
    def balance_of(self, address: Address) -> U128:
        """Get fraction balance of a user."""
        self._require_initialized()
        return self.storage.get(f"bal_{address}", U128(0))

    @view
    def get_buyout_state(self) -> Map:
        """Get the current buyout parameters."""
        res = Map(self.env)
        res.set("active", self.storage.get("buyout_active", False))
        res.set("completed", self.storage.get("buyout_completed", False))
        if self.storage.get("buyout_active", False):
            res.set("proposer", self.storage.get("buyout_proposer"))
            res.set("price", self.storage.get("buyout_price"))
            res.set("end", self.storage.get("buyout_end"))
            res.set("yes_votes", self.storage.get("buyout_votes_yes"))
            res.set("no_votes", self.storage.get("buyout_votes_no"))
        return res

    # --- INTERNAL HELPERS ---

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_not_paused(self):
        if self.storage.get("paused", False):
            raise ContractError.PAUSED

    def _require_admin(self, caller: Address):
        if caller != self.storage.get("admin"):
            raise ContractError.UNAUTHORIZED

    def _get_now(self) -> U64:
        return self.env.ledger_timestamp()
