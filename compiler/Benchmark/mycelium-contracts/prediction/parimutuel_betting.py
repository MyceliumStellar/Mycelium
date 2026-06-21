"""
Parimutuel Betting — Betting pool distributions, odd calculations, payout ratios, commission deductions.

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
    MARKET_RESOLVED = 4
    MARKET_NOT_RESOLVED = 5
    INVALID_OPTION = 6
    BETTING_CLOSED = 7
    INSUFFICIENT_BALANCE = 8
    ZERO_AMOUNT = 9
    NO_BETS_ON_WINNER = 10


@contract
class ParimutuelBetting:
    """A parimutuel betting pool contract with dynamic odds calculations and fee commissions."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        collateral_token: Address,
        oracle: Address,
        options: Vec,
        commission_bps: U64,
        betting_deadline: U64,
    ):
        """Initialize the parimutuel pool.

        Args:
            admin: Admin address.
            collateral_token: Backing token address.
            oracle: Resolution oracle address.
            options: List of betting options (Symbols).
            commission_bps: Commission percentage in basis points.
            betting_deadline: Time after which bets are disabled.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("token", collateral_token)
        self.storage.set("oracle", oracle)
        self.storage.set("options", options)
        self.storage.set("commission_bps", commission_bps)
        self.storage.set("betting_deadline", betting_deadline)

        # Totals
        self.storage.set("total_pool", U128(0))
        for i in range(len(options)):
            opt = options.get(i)
            self.storage.set(("option_pool", opt), U128(0))

        self.storage.set("resolved", False)
        self.storage.set("winning_option", Symbol(""))
        self.storage.set("collected_commission", U128(0))
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "deadline": betting_deadline,
            "options_count": len(options),
        })

    @external
    def place_bet(self, bettor: Address, option: Symbol, amount: U128) -> U128:
        """Place a bet on one of the parimutuel options.

        Args:
            bettor: Bettor address.
            option: Bet option symbol.
            amount: Collateral amount to stake.
        """
        self._require_initialized()
        self._require_not_resolved()
        bettor.require_auth()

        now = self.env.ledger().timestamp()
        deadline = self.storage.get("betting_deadline")
        if now >= deadline:
            raise ContractError.BETTING_CLOSED

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        if not self._is_valid_option(option):
            raise ContractError.INVALID_OPTION

        token = self.storage.get("token")
        success = self.env.invoke_contract(token, "transfer", [bettor, self.env.current_contract_address(), amount])
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        # Update Pools
        self.storage.set("total_pool", self.storage.get("total_pool") + amount)
        
        opt_pool = self.storage.get(("option_pool", option))
        self.storage.set(("option_pool", option), opt_pool + amount)

        # Update User bet
        user_bet = self.storage.get(("bettor_stake", bettor, option), U128(0))
        self.storage.set(("bettor_stake", bettor, option), user_bet + amount)

        self.env.emit_event("bet_placed", {
            "bettor": bettor,
            "option": option,
            "amount": amount,
        })

        return amount

    @external
    def resolve(self, caller: Address, winning_option: Symbol):
        """Resolve the winning option. Deducts commission from the total pool.

        Args:
            caller: Resolution oracle.
            winning_option: Resolved winning symbol (or Symbol("VOID") if cancelled).
        """
        self._require_initialized()
        self._require_not_resolved()
        caller.require_auth()

        oracle = self.storage.get("oracle")
        if caller != oracle:
            raise ContractError.UNAUTHORIZED

        is_void = winning_option == Symbol("VOID")
        if not is_void and not self._is_valid_option(winning_option):
            raise ContractError.INVALID_OPTION

        total_pool = self.storage.get("total_pool")

        if not is_void:
            opt_pool = self.storage.get(("option_pool", winning_option))
            if opt_pool == U128(0) and total_pool > U128(0):
                # Edge case: No bets on the winner, refund everyone (act as void)
                winning_option = Symbol("VOID")
                is_void = True

        if not is_void:
            commission_bps = self.storage.get("commission_bps")
            commission = (total_pool * U128(commission_bps)) / U128(10000)
            self.storage.set("collected_commission", commission)

        self.storage.set("winning_option", winning_option)
        self.storage.set("resolved", True)

        self.env.emit_event("market_resolved", {
            "winning_option": winning_option,
        })

    @external
    def claim_winnings(self, claimant: Address) -> U128:
        """Claim parimutuel payout for winning bets, or get refund if voided.

        Args:
            claimant: Bettor address.
        """
        self._require_initialized()
        self._require_resolved()
        claimant.require_auth()

        winner = self.storage.get("winning_option")
        token = self.storage.get("token")

        if winner == Symbol("VOID"):
            # Refund all bets placed by claimant
            options = self.storage.get("options")
            total_refund = U128(0)
            for i in range(len(options)):
                opt = options.get(i)
                user_bet = self.storage.get(("bettor_stake", claimant, opt), U128(0))
                if user_bet > U128(0):
                    total_refund = total_refund + user_bet
                    self.storage.set(("bettor_stake", claimant, opt), U128(0))

            if total_refund == U128(0):
                raise ContractError.ZERO_AMOUNT

            self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), claimant, total_refund])
            self.env.emit_event("winnings_claimed", {
                "claimant": claimant,
                "payout": total_refund,
            })
            return total_refund

        else:
            user_bet = self.storage.get(("bettor_stake", claimant, winner), U128(0))
            if user_bet == U128(0):
                raise ContractError.ZERO_AMOUNT

            # Clear stakes
            options = self.storage.get("options")
            for i in range(len(options)):
                opt = options.get(i)
                self.storage.set(("bettor_stake", claimant, opt), U128(0))

            total_pool = self.storage.get("total_pool")
            commission = self.storage.get("collected_commission")
            net_pool = total_pool - commission
            winner_pool = self.storage.get(("option_pool", winner))

            # Payout = user_bet * net_pool / winner_pool
            payout = (user_bet * net_pool) / winner_pool

            self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), claimant, payout])
            self.env.emit_event("winnings_claimed", {
                "claimant": claimant,
                "payout": payout,
            })
            return payout

    @external
    def withdraw_commission(self, caller: Address) -> U128:
        """Withdraw commission. Only admin.

        Args:
            caller: Admin.
        """
        self._require_initialized()
        caller.require_auth()

        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

        commission = self.storage.get("collected_commission")
        if commission == U128(0):
            raise ContractError.ZERO_AMOUNT

        self.storage.set("collected_commission", U128(0))
        token = self.storage.get("token")
        self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), admin, commission])

        self.env.emit_event("commission_withdrawn", {
            "amount": commission,
        })

        return commission

    @view
    def get_odds(self) -> Map:
        """Calculate dynamic odds for each option (scaled by 100, e.g. 250 = 2.50x odds)."""
        options = self.storage.get("options")
        total_pool = self.storage.get("total_pool")

        odds = Map()
        if total_pool == U128(0):
            for i in range(len(options)):
                odds.set(options.get(i), U64(100))
            return odds

        for i in range(len(options)):
            opt = options.get(i)
            opt_pool = self.storage.get(("option_pool", opt))
            if opt_pool == U128(0):
                odds.set(opt, U64(0)) # Infinite/uncalculated odds
            else:
                opt_odds = (total_pool * U128(100)) / opt_pool
                odds.set(opt, U64(opt_odds))

        return odds

    @view
    def get_pool_status(self) -> Map:
        """Get total pool and individual option pools."""
        options = self.storage.get("options")
        res = Map()
        res.set("total_pool", self.storage.get("total_pool"))
        for i in range(len(options)):
            opt = options.get(i)
            res.set(opt, self.storage.get(("option_pool", opt)))
        return res

    @view
    def get_bettor_stakes(self, bettor: Address) -> Map:
        """Get stakes placed by a bettor on all options."""
        options = self.storage.get("options")
        res = Map()
        for i in range(len(options)):
            opt = options.get(i)
            res.set(opt, self.storage.get(("bettor_stake", bettor, opt), U128(0)))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_not_resolved(self):
        if self.storage.get("resolved", False):
            raise ContractError.MARKET_RESOLVED

    def _require_resolved(self):
        if not self.storage.get("resolved", False):
            raise ContractError.MARKET_NOT_RESOLVED

    def _is_valid_option(self, option: Symbol) -> Bool:
        options = self.storage.get("options")
        for i in range(len(options)):
            if options.get(i) == option:
                return True
        return False
