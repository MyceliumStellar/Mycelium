"""
NFT Collateral Lending — Borrow against NFTs with Floor Price Oracle and Dutch Auction liquidation.

Mycelium Smart Contract for Stellar
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


# ── Error Codes ──────────────────────────────────────────────────────────────

class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    NFT_NOT_SUPPORTED = 4
    NFT_ALREADY_SUPPORTED = 5
    LTV_EXCEEDED = 6
    POSITION_NOT_FOUND = 7
    POSITION_NOT_UNDERWATER = 8
    AUCTION_NOT_ACTIVE = 9
    AUCTION_EXPIRED = 10
    INSUFFICIENT_PAYMENT = 11
    ZERO_AMOUNT = 12
    ZERO_PRICE = 13
    OVERFLOW = 14
    INVALID_STATUS = 15


# ── Constants ────────────────────────────────────────────────────────────────

WAD = U128(1_000_000_000_000_000_000)  # 1e18 scale
SECONDS_PER_YEAR = U128(31_536_000)
BPS_DENOMINATOR = U128(10000)
AUCTION_DURATION = U64(86400)          # 24 hours Dutch Auction decay


@contract
class NFTCollateralLending:
    """
    Escrow-based NFT collateralized loan contract.
    Borrowers lock supported NFTs to borrow stablecoins.
    If the loan health factor drops below 1.0 (assessed by NFT floor price),
    the NFT is placed in a Dutch auction liquidation where the price decays
    towards the outstanding debt.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ── Admin Operations ─────────────────────────────────────────────────────

    @external
    def initialize(self, admin: Address, stablecoin: Address):
        """
        Initializes the contract parameters.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("stablecoin", stablecoin)
        self.storage.set("nft_contracts_list", Vec())
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "stablecoin": stablecoin,
        })

    @external
    def support_nft_collection(
        self,
        caller: Address,
        nft_contract: Address,
        ltv_bps: U128,
        liq_threshold_bps: U128,
        interest_rate_bps: U128,
    ):
        """
        Approves an NFT collection to be used as collateral. Admin-only.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        collections = self.storage.get("nft_contracts_list")
        for i in range(len(collections)):
            if collections[i] == nft_contract:
                raise ContractError.NFT_ALREADY_SUPPORTED

        config = {
            "ltv": ltv_bps,
            "liq_threshold": liq_threshold_bps,
            "interest_rate": interest_rate_bps,
        }
        self.storage.set(f"nft_config:{nft_contract}", config)

        state = {
            "total_borrow_shares": U128(0),
            "borrow_index": WAD,
            "last_update_time": self.env.ledger().timestamp(),
        }
        self.storage.set(f"nft_state:{nft_contract}", state)

        # Set default mock floor price to 100 USD (8 decimals)
        self.storage.set(f"nft_floor:{nft_contract}", U128(10_000_000_000))

        collections.append(nft_contract)
        self.storage.set("nft_contracts_list", collections)

        self.env.emit_event("nft_collection_supported", {
            "nft_contract": nft_contract,
            "ltv": ltv_bps,
            "liq_threshold": liq_threshold_bps,
        })

    @external
    def set_nft_floor_price(self, caller: Address, nft_contract: Address, price_usd: U128):
        """
        Updates the floor price of an NFT collection in USD (8 decimals). Admin/Oracle-only.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        self._get_nft_config(nft_contract)  # Verification
        self.storage.set(f"nft_floor:{nft_contract}", price_usd)

        self.env.emit_event("floor_price_updated", {
            "nft_contract": nft_contract,
            "floor_price": price_usd,
        })

    # ── Borrower Operations ──────────────────────────────────────────────────

    @external
    def borrow(self, caller: Address, nft_contract: Address, token_id: U256, amount: U128):
        """
        Locks borrower's NFT in escrow to draw a stablecoin loan.
        """
        caller.require_auth()
        self._require_initialized()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        config = self._get_nft_config(nft_contract)
        self._accrue_interest(nft_contract)
        state = self._get_nft_state(nft_contract)

        # Verify token is not already locked
        position_key = f"nft_position:{nft_contract}:{token_id}"
        if self.storage.has(position_key):
            raise ContractError.INVALID_STATUS

        # Verify floor price value
        floor_price = self.storage.get(f"nft_floor:{nft_contract}", U128(0))
        if floor_price == U128(0):
            raise ContractError.ZERO_PRICE

        # floor_price has 8 decimals. Convert NFT collateral capacity to stablecoin decimals (assuming 7 decimals)
        # capacity = floor_price_usd * ltv_bps / 10000
        # capacity in 7 decimals = capacity * 10^7 / 10^8
        capacity_usd = (floor_price * config["ltv"]) // BPS_DENOMINATOR
        max_borrow = capacity_usd // U128(10)  # Adjusting 8 decimals down to 7 decimals for standard stablecoin
        
        if amount > max_borrow:
            raise ContractError.LTV_EXCEEDED

        # Lock NFT: call transfer(from, to, token_id) on the NFT contract
        # Signature: transfer(sender, recipient, token_id)
        self.env.call(nft_contract, "transfer", [caller, self.env.current_contract(), token_id])

        # Record position
        shares = (amount * WAD) // state["borrow_index"]
        position = {
            "borrower": caller,
            "borrow_shares": shares,
            "status": U64(1),  # 1 = Borrowed
        }
        self.storage.set(position_key, position)

        # Update pool state
        state["total_borrow_shares"] += shares
        self.storage.set(f"nft_state:{nft_contract}", state)

        # Mint stablecoin to borrower
        stablecoin = self.storage.get("stablecoin")
        self.env.mint(stablecoin, caller, amount)

        self.env.emit_event("nft_borrowed", {
            "borrower": caller,
            "nft_contract": nft_contract,
            "token_id": token_id,
            "amount": amount,
            "shares": shares,
        })

    @external
    def repay(self, caller: Address, nft_contract: Address, token_id: U256, amount: U128):
        """
        Repays borrow position. Returns the locked NFT when loan is fully settled.
        """
        caller.require_auth()
        self._require_initialized()

        position_key = f"nft_position:{nft_contract}:{token_id}"
        position = self._get_position(position_key)
        
        if position["status"] != U64(1):
            raise ContractError.INVALID_STATUS

        self._accrue_interest(nft_contract)
        state = self._get_nft_state(nft_contract)

        debt = (position["borrow_shares"] * state["borrow_index"]) // WAD

        # Cap repayment to current outstanding debt
        repay_amount = amount
        if repay_amount > debt:
            repay_amount = debt

        shares_to_burn = (repay_amount * WAD) // state["borrow_index"]
        if shares_to_burn > position["borrow_shares"]:
            shares_to_burn = position["borrow_shares"]

        position["borrow_shares"] -= shares_to_burn
        state["total_borrow_shares"] -= shares_to_burn

        # Save updates
        self.storage.set(f"nft_state:{nft_contract}", state)

        # Burn paid stablecoins
        stablecoin = self.storage.get("stablecoin")
        self.env.burn(stablecoin, caller, repay_amount)

        # Return NFT if fully repaid
        if position["borrow_shares"] == U128(0):
            self.storage.remove(position_key)
            self.env.call(nft_contract, "transfer", [self.env.current_contract(), position["borrower"], token_id])
            self.env.emit_event("nft_reclaimed", {
                "owner": position["borrower"],
                "nft_contract": nft_contract,
                "token_id": token_id,
            })
        else:
            self.storage.set(position_key, position)
            self.env.emit_event("loan_repaid", {
                "nft_contract": nft_contract,
                "token_id": token_id,
                "repaid": repay_amount,
                "remaining_debt": debt - repay_amount,
            })

    # ── Liquidation Operations (Dutch Auction) ───────────────────────────────

    @external
    def trigger_liquidation(self, caller: Address, nft_contract: Address, token_id: U256):
        """
        Triggers Dutch auction liquidation of an under-collateralized NFT position.
        """
        caller.require_auth()
        self._require_initialized()

        position_key = f"nft_position:{nft_contract}:{token_id}"
        position = self._get_position(position_key)

        if position["status"] != U64(1):
            raise ContractError.INVALID_STATUS

        config = self._get_nft_config(nft_contract)
        self._accrue_interest(nft_contract)
        state = self._get_nft_state(nft_contract)

        debt = (position["borrow_shares"] * state["borrow_index"]) // WAD
        floor_price = self.storage.get(f"nft_floor:{nft_contract}", U128(0))
        if floor_price == U128(0):
            raise ContractError.ZERO_PRICE

        # Evaluate safety
        # limit in stablecoin decimals = floor_usd * threshold_bps / 100000
        capacity_usd = (floor_price * config["liq_threshold"]) // BPS_DENOMINATOR
        liq_limit = capacity_usd // U128(10)  # Scale to stablecoin decimals (assuming 7)

        if debt <= liq_limit:
            raise ContractError.POSITION_NOT_UNDERWATER

        # Start Dutch Auction
        # Starting price is 130% of floor price or 130% of debt, whichever is greater
        start_price = (max(debt, floor_price // U128(10)) * U128(13000)) // BPS_DENOMINATOR
        min_price = debt  # Price decays down to outstanding debt

        now = self.env.ledger().timestamp()

        auction = {
            "start_time": now,
            "start_price": start_price,
            "min_price": min_price,
            "debt": debt,
        }
        self.storage.set(f"auction:{nft_contract}:{token_id}", auction)

        position["status"] = U64(2)  # Under Liquidation
        self.storage.set(position_key, position)

        # Deduct from pool shares (seized from active lending state)
        state["total_borrow_shares"] -= position["borrow_shares"]
        self.storage.set(f"nft_state:{nft_contract}", state)

        self.env.emit_event("liquidation_triggered", {
            "nft_contract": nft_contract,
            "token_id": token_id,
            "debt": debt,
            "start_price": start_price,
        })

    @external
    def buy_liquidated_nft(self, caller: Address, nft_contract: Address, token_id: U256):
        """
        Purchases an NFT in Dutch auction. Repays debt and refunds excess to borrower.
        """
        caller.require_auth()
        self._require_initialized()

        position_key = f"nft_position:{nft_contract}:{token_id}"
        position = self._get_position(position_key)
        if position["status"] != U64(2):
            raise ContractError.INVALID_STATUS

        auction_key = f"auction:{nft_contract}:{token_id}"
        auction = self.storage.get(auction_key, None)
        if auction is None:
            raise ContractError.AUCTION_NOT_ACTIVE

        # Calculate current Dutch decay price
        now = self.env.ledger().timestamp()
        start_time = auction["start_time"]
        start_price = auction["start_price"]
        min_price = auction["min_price"]
        
        elapsed = now - start_time
        if elapsed >= AUCTION_DURATION:
            current_price = min_price
        else:
            decay = ((start_price - min_price) * U128(elapsed)) // U128(AUCTION_DURATION)
            current_price = start_price - decay

        # Burn debt portion
        stablecoin = self.storage.get("stablecoin")
        self.env.burn(stablecoin, caller, auction["debt"])

        # Transfer excess price to borrower
        excess = current_price - auction["debt"] if current_price > auction["debt"] else U128(0)
        if excess > U128(0):
            self.env.transfer(caller, position["borrower"], stablecoin, excess)

        # Deliver NFT to buyer
        self.env.call(nft_contract, "transfer", [self.env.current_contract(), caller, token_id])

        # Cleanup states
        self.storage.remove(position_key)
        self.storage.remove(auction_key)

        self.env.emit_event("liquidation_settled", {
            "nft_contract": nft_contract,
            "token_id": token_id,
            "buyer": caller,
            "purchase_price": current_price,
            "repaid_debt": auction["debt"],
            "returned_to_borrower": excess,
        })

    # ── View Functions ───────────────────────────────────────────────────────

    @view
    def get_position_info(self, nft_contract: Address, token_id: U256) -> Map:
        """Returns details of an active borrowing position."""
        position_key = f"nft_position:{nft_contract}:{token_id}"
        position = self._get_position(position_key)
        state = self._get_nft_state(nft_contract)
        
        debt = (position["borrow_shares"] * state["borrow_index"]) // WAD
        return {
            "borrower": position["borrower"],
            "debt": debt,
            "status": position["status"],
        }

    @view
    def get_auction_price(self, nft_contract: Address, token_id: U256) -> U128:
        """Returns the current decaying price of an active Dutch auction."""
        auction_key = f"auction:{nft_contract}:{token_id}"
        auction = self.storage.get(auction_key, None)
        if auction is None:
            raise ContractError.AUCTION_NOT_ACTIVE

        now = self.env.ledger().timestamp()
        start_time = auction["start_time"]
        start_price = auction["start_price"]
        min_price = auction["min_price"]
        
        elapsed = now - start_time
        if elapsed >= AUCTION_DURATION:
            return min_price
        else:
            decay = ((start_price - min_price) * U128(elapsed)) // U128(AUCTION_DURATION)
            return start_price - decay

    # ── Internal Helpers ─────────────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _get_nft_config(self, nft_contract: Address) -> Map:
        config = self.storage.get(f"nft_config:{nft_contract}", None)
        if config is None:
            raise ContractError.NFT_NOT_SUPPORTED
        return config

    def _get_nft_state(self, nft_contract: Address) -> Map:
        state = self.storage.get(f"nft_state:{nft_contract}", None)
        if state is None:
            raise ContractError.NFT_NOT_SUPPORTED
        return state

    def _get_position(self, key: str) -> Map:
        position = self.storage.get(key, None)
        if position is None:
            raise ContractError.POSITION_NOT_FOUND
        return position

    def _accrue_interest(self, nft_contract: Address):
        """Accrues borrowing interest over time for an NFT collection pool."""
        state = self._get_nft_state(nft_contract)
        config = self._get_nft_config(nft_contract)
        now = self.env.ledger().timestamp()

        time_elapsed = U128(now - state["last_update_time"])
        if time_elapsed == U128(0):
            return

        rate = config["interest_rate"]
        interest_factor = (rate * time_elapsed * WAD) // (SECONDS_PER_YEAR * BPS_DENOMINATOR)

        state["borrow_index"] = (state["borrow_index"] * (WAD + interest_factor)) // WAD
        state["last_update_time"] = now

        self.storage.set(f"nft_state:{nft_contract}", state)
