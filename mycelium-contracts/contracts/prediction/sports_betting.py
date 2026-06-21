"""
Sports Betting — Handicap spreads, match outcome bets, odds tracking, cancel/void events rules.

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
    EVENT_RESOLVED = 4
    EVENT_NOT_RESOLVED = 5
    BETTING_CLOSED = 6
    INVALID_BET_TYPE = 7
    INVALID_OUTCOME = 8
    INSUFFICIENT_LIQUIDITY = 9
    INSUFFICIENT_BALANCE = 10
    ZERO_AMOUNT = 11


class BetType:
    MONEYLINE = 1
    SPREAD = 2


@contract
class SportsBetting:
    """A sports betting bookmaker contract backing Moneyline and Spread bets with LP capital."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        collateral_token: Address,
        bookmaker_fee_bps: U64,
        betting_deadline: U64,
        spread_handicap: I128, # Handicap for Team A in bps (e.g. -150 for -1.5, +150 for +1.5)
    ):
        """Initialize the sports betting contract.

        Args:
            admin: Admin/Bookmaker controller.
            collateral_token: Backing token.
            bookmaker_fee_bps: Platform fee bps.
            betting_deadline: Time after which betting is disabled.
            spread_handicap: Handicap for Team A (scaled to bps, e.g. -1.5 points = -150).
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        self.storage.set("admin", admin)
        self.storage.set("token", collateral_token)
        self.storage.set("fee_bps", bookmaker_fee_bps)
        self.storage.set("deadline", betting_deadline)
        self.storage.set("spread_handicap", spread_handicap)

        # Odds (multiplier scaled by 100, e.g. 2.0x = 200)
        self.storage.set("odds_team_a", U64(200))
        self.storage.set("odds_team_b", U64(200))
        self.storage.set("odds_draw", U64(300))
        self.storage.set("odds_spread_a", U64(190))
        self.storage.set("odds_spread_b", U64(190))

        # Liability tracking to prevent bookmaker insolvency
        self.storage.set("max_liability", U128(0))
        self.storage.set("current_liability", U128(0))
        self.storage.set("bookmaker_capital", U128(0))
        self.storage.set("total_lp_shares", U128(0))

        # Resolution score
        self.storage.set("resolved", False)
        self.storage.set("score_a", U64(0))
        self.storage.set("score_b", U64(0))
        self.storage.set("voided", False)
        self.storage.set("bet_counter", U64(0))

        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "deadline": betting_deadline,
        })

    @external
    def add_liquidity(self, lp: Address, amount: U128) -> U128:
        """Provide liquidity to back the bookmaker's betting liability.

        Args:
            lp: Liquidity provider.
            amount: Token amount to deposit.
        """
        self._require_initialized()
        lp.require_auth()

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        token = self.storage.get("token")
        success = self.env.invoke_contract(token, "transfer", [lp, self.env.current_contract_address(), amount])
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        capital = self.storage.get("bookmaker_capital")
        total_lp = self.storage.get("total_lp_shares")

        shares = U128(0)
        if total_lp == U128(0):
            shares = amount
        else:
            shares = (amount * total_lp) / capital

        self.storage.set("bookmaker_capital", capital + amount)
        self.storage.set("total_lp_shares", total_lp + shares)

        lp_bal = self.storage.get(("lp_balance", lp), U128(0))
        self.storage.set(("lp_balance", lp), lp_bal + shares)

        self.env.emit_event("liquidity_added", {
            "lp": lp,
            "amount": amount,
            "shares": shares,
        })

        return shares

    @external
    def remove_liquidity(self, lp: Address, shares: U128) -> U128:
        """Withdraw liquidity. Cannot remove if remaining capital is below active liabilities.

        Args:
            lp: LP address.
            shares: LP shares to burn.
        """
        self._require_initialized()
        lp.require_auth()

        if shares == U128(0):
            raise ContractError.ZERO_AMOUNT

        total_lp = self.storage.get("total_lp_shares")
        lp_bal = self.storage.get(("lp_balance", lp), U128(0))
        if lp_bal < shares:
            raise ContractError.INSUFFICIENT_BALANCE

        capital = self.storage.get("bookmaker_capital")
        payout = (shares * capital) / total_lp

        # Check liability constraint (active bet liability must be covered by remaining capital)
        liability = self.storage.get("current_liability")
        remaining_capital = capital - payout
        if remaining_capital < liability and not self.storage.get("resolved"):
            raise ContractError.INSUFFICIENT_LIQUIDITY

        self.storage.set("bookmaker_capital", remaining_capital)
        self.storage.set("total_lp_shares", total_lp - shares)
        self.storage.set(("lp_balance", lp), lp_bal - shares)

        token = self.storage.get("token")
        self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), lp, payout])

        self.env.emit_event("liquidity_removed", {
            "lp": lp,
            "payout": payout,
        })

        return payout

    @external
    def update_odds(
        self,
        caller: Address,
        odds_team_a: U64,
        odds_team_b: U64,
        odds_draw: U64,
        odds_spread_a: U64,
        odds_spread_b: U64,
    ):
        """Update betting odds multipliers. Only admin.

        Args:
            caller: Admin.
            odds_team_a: Team A moneyline odds (bps).
            odds_team_b: Team B moneyline odds (bps).
            odds_draw: Draw odds (bps).
            odds_spread_a: Team A spread odds (bps).
            odds_spread_b: Team B spread odds (bps).
        """
        self._require_initialized()
        self._require_admin(caller)

        self.storage.set("odds_team_a", odds_team_a)
        self.storage.set("odds_team_b", odds_team_b)
        self.storage.set("odds_draw", odds_draw)
        self.storage.set("odds_spread_a", odds_spread_a)
        self.storage.set("odds_spread_b", odds_spread_b)

        self.env.emit_event("odds_updated", {
            "team_a": odds_team_a,
            "team_b": odds_team_b,
        })

    @external
    def place_bet(self, bettor: Address, bet_type: U64, selection: Symbol, amount: U128) -> U64:
        """Place a sports bet (Moneyline or Spread) with fixed odds.

        Args:
            bettor: Bettor.
            bet_type: 1 = Moneyline, 2 = Spread.
            selection: TEAM_A, TEAM_B, DRAW, SPREAD_A, SPREAD_B.
            amount: Bet stake.
        """
        self._require_initialized()
        if self.storage.get("resolved"):
            raise ContractError.EVENT_RESOLVED

        bettor.require_auth()

        now = self.env.ledger().timestamp()
        if now >= self.storage.get("deadline"):
            raise ContractError.BETTING_CLOSED

        if amount == U128(0):
            raise ContractError.ZERO_AMOUNT

        # Retrieve odds
        odds = U64(0)
        if bet_type == BetType.MONEYLINE:
            if selection == Symbol("TEAM_A"):
                odds = self.storage.get("odds_team_a")
            elif selection == Symbol("TEAM_B"):
                odds = self.storage.get("odds_team_b")
            elif selection == Symbol("DRAW"):
                odds = self.storage.get("odds_draw")
            else:
                raise ContractError.INVALID_OUTCOME
        elif bet_type == BetType.SPREAD:
            if selection == Symbol("SPREAD_A"):
                odds = self.storage.get("odds_spread_a")
            elif selection == Symbol("SPREAD_B"):
                odds = self.storage.get("odds_spread_b")
            else:
                raise ContractError.INVALID_OUTCOME
        else:
            raise ContractError.INVALID_BET_TYPE

        # Calculate potential payout liability
        # payout = amount * odds / 100
        potential_payout = (amount * U128(odds)) / U128(100)
        net_liability = potential_payout - amount

        capital = self.storage.get("bookmaker_capital")
        current_liability = self.storage.get("current_liability")

        # Verify bookmaker has capital to cover net liability
        if current_liability + net_liability > capital:
            raise ContractError.INSUFFICIENT_LIQUIDITY

        token = self.storage.get("token")
        success = self.env.invoke_contract(token, "transfer", [bettor, self.env.current_contract_address(), amount])
        if not success:
            raise ContractError.INSUFFICIENT_BALANCE

        # Update liability
        self.storage.set("current_liability", current_liability + net_liability)

        # Store bet details
        bet_id = self.storage.get("bet_counter") + U64(1)
        self.storage.set("bet_counter", bet_id)

        bet = Map()
        bet.set("bettor", bettor)
        bet.set("bet_type", bet_type)
        bet.set("selection", selection)
        bet.set("amount", amount)
        bet.set("odds", odds)
        bet.set("net_liability", net_liability)
        bet.set("claimed", False)

        self.storage.set(("bet", bet_id), bet)

        self.env.emit_event("bet_placed", {
            "bet_id": bet_id,
            "bettor": bettor,
            "selection": selection,
            "odds": odds,
        })

        return bet_id

    @external
    def resolve_event(self, caller: Address, score_a: U64, score_b: U64, void: Bool):
        """Resolve the sports match scores. Calculates outcomes.

        Args:
            caller: Admin.
            score_a: Score of Team A.
            score_b: Score of Team B.
            void: Set True to void/cancel event.
        """
        self._require_initialized()
        if self.storage.get("resolved"):
            raise ContractError.EVENT_RESOLVED
        self._require_admin(caller)

        self.storage.set("score_a", score_a)
        self.storage.set("score_b", score_b)
        self.storage.set("voided", void)
        self.storage.set("resolved", True)

        self.env.emit_event("event_resolved", {
            "score_a": score_a,
            "score_b": score_b,
            "voided": void,
        })

    @external
    def claim_bet(self, caller: Address, bet_id: U64) -> U128:
        """Claim payout for a specific sports bet.

        Args:
            caller: Trigger address.
            bet_id: Bet ID to claim.
        """
        self._require_initialized()
        if not self.storage.get("resolved"):
            raise ContractError.EVENT_NOT_RESOLVED

        bet = self.storage.get(("bet", bet_id), None)
        if bet is None:
            raise ContractError.INVALID_OUTCOME
        if bet.get("claimed"):
            raise ContractError.ALREADY_INITIALIZED

        # Mark as claimed immediately
        bet.set("claimed", True)
        self.storage.set(("bet", bet_id), bet)

        token = self.storage.get("token")
        bettor = bet.get("bettor")
        amount = bet.get("amount")
        net_liability = bet.get("net_liability")
        payout = U128(0)

        is_void = self.storage.get("voided")

        if is_void:
            # Full refund of stake
            payout = amount
            # Deduct liability
            self.storage.set("current_liability", self.storage.get("current_liability") - net_liability)
        else:
            # Evaluate winner
            bet_type = bet.get("bet_type")
            selection = bet.get("selection")
            score_a = self.storage.get("score_a")
            score_b = self.storage.get("score_b")
            odds = bet.get("odds")

            won = False

            if bet_type == BetType.MONEYLINE:
                if selection == Symbol("TEAM_A") and score_a > score_b:
                    won = True
                elif selection == Symbol("TEAM_B") and score_b > score_a:
                    won = True
                elif selection == Symbol("DRAW") and score_a == score_b:
                    won = True
            elif bet_type == BetType.SPREAD:
                handicap = self.storage.get("spread_handicap")
                # score_a points + handicap vs score_b
                score_a_adjusted = I128(score_a) * I128(100) + handicap
                score_b_scaled = I128(score_b) * I128(100)

                if selection == Symbol("SPREAD_A") and score_a_adjusted > score_b_scaled:
                    won = True
                elif selection == Symbol("SPREAD_B") and score_b_scaled > score_a_adjusted:
                    won = True

            if won:
                payout = (amount * U128(odds)) / U128(100)
                # Deduct from capital
                capital = self.storage.get("bookmaker_capital")
                self.storage.set("bookmaker_capital", capital - net_liability)
            else:
                # Bettor lost. Staked amount remains in bookmaker capital as earnings.
                capital = self.storage.get("bookmaker_capital")
                self.storage.set("bookmaker_capital", capital + amount)

        if payout > U128(0):
            self.env.invoke_contract(token, "transfer", [self.env.current_contract_address(), bettor, payout])

        self.env.emit_event("bet_settled", {
            "bet_id": bet_id,
            "bettor": bettor,
            "payout": payout,
        })

        return payout

    @view
    def get_bet(self, bet_id: U64) -> Map:
        """Get bet details."""
        return self.storage.get(("bet", bet_id))

    @view
    def get_liability_status(self) -> Map:
        """Get liability and capital details."""
        res = Map()
        res.set("bookmaker_capital", self.storage.get("bookmaker_capital"))
        res.set("current_liability", self.storage.get("current_liability"))
        res.set("total_lp_shares", self.storage.get("total_lp_shares"))
        return res

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                    #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        caller.require_auth()
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED
