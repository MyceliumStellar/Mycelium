"""
RateLimiter — Token amount limits, window durations, category overrides, daily logs.

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
    LIMIT_EXCEEDED = 4
    INVALID_WINDOW = 5
    INVALID_AMOUNT = 6

@contract
class RateLimiter:
    """
    Volume-based transaction rate limiter.
    
    Restricts user transfer volumes per rolling or static time window.
    Supports user category/tier overrides and manual override capabilities.
    """
    
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, default_window: U64, default_limit: U128):
        """
        Initializes the rate limiter rules.
        
        Args:
            admin: Admin address controlling rate limits.
            default_window: Time span in seconds (e.g. 86400 for 1 day).
            default_limit: Maximum allowed volume per window by default.
        """
        admin.require_auth()
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED
            
        if default_window == U64(0):
            raise ContractError.INVALID_WINDOW
            
        self.storage.set("admin", admin)
        self.storage.set("def_window", default_window)
        self.storage.set("def_limit", default_limit)
        self.storage.set("initialized", True)
        
        self.env.emit_event(
            "initialized", 
            {"admin": admin, "default_window": default_window, "default_limit": default_limit}
        )

    @external
    def set_default_policy(self, caller: Address, window: U64, limit: U128) -> Bool:
        """
        Updates the global rate limit policy settings.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        if window == U64(0):
            raise ContractError.INVALID_WINDOW
            
        self.storage.set("def_window", window)
        self.storage.set("def_limit", limit)
        
        self.env.emit_event("default_policy_updated", {"window": window, "limit": limit})
        return True

    @external
    def set_category_policy(
        self, 
        caller: Address, 
        category: Symbol, 
        limit: U128, 
        window: U64
    ) -> Bool:
        """
        Assigns standard rate limit policies for a specific tier/category.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        if window == U64(0):
            raise ContractError.INVALID_WINDOW
            
        self.storage.set("cat_limit:" + str(category), limit)
        self.storage.set("cat_window:" + str(category), window)
        
        self.env.emit_event(
            "category_policy_updated", 
            {"category": category, "limit": limit, "window": window}
        )
        return True

    @external
    def assign_account_category(self, caller: Address, account: Address, category: Symbol) -> Bool:
        """
        Assigns a user account to a policy category (tier).
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        self.storage.set("acc_cat:" + str(account), category)
        self.env.emit_event("account_category_assigned", {"account": account, "category": category})
        return True

    @external
    def set_custom_limit(
        self, 
        caller: Address, 
        account: Address, 
        limit: U128, 
        window: U64
    ) -> Bool:
        """
        Overrides categories with a custom rate limit for a specific user.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        if window == U64(0):
            raise ContractError.INVALID_WINDOW
            
        self.storage.set("custom_limit:" + str(account), limit)
        self.storage.set("custom_window:" + str(account), window)
        
        self.env.emit_event(
            "custom_limit_set", 
            {"account": account, "limit": limit, "window": window}
        )
        return True

    @external
    def record_transaction(self, caller: Address, account: Address, amount: U128) -> Bool:
        """
        Checks and records a transaction amount, updating user window consumption.
        
        Throws error if user exceeds allowed rate limit threshold.
        
        Args:
            caller: Authorizing caller (e.g. the transaction dispatcher or token contract).
            account: User sending the funds.
            amount: Transaction volume.
        """
        caller.require_auth()
        self._require_initialized()
        
        if amount == U128(0):
            raise ContractError.INVALID_AMOUNT
            
        limit, window = self._get_policy_for(account)
        current_time = self.env.ledger().timestamp()
        
        win_start_key = "win_start:" + str(account)
        win_vol_key = "win_vol:" + str(account)
        
        window_start = self.storage.get(win_start_key, U64(0))
        current_volume = self.storage.get(win_vol_key, U128(0))
        
        # Check window reset
        if current_time >= window_start + window:
            window_start = current_time
            current_volume = U128(0)
            self.storage.set(win_start_key, current_time)
            
        # Verify limit
        new_volume = current_volume + amount
        if new_volume > limit:
            raise ContractError.LIMIT_EXCEEDED
            
        self.storage.set(win_vol_key, new_volume)
        
        self.env.emit_event(
            "volume_recorded", 
            {
                "account": account, 
                "amount": amount, 
                "total_volume": new_volume, 
                "window_end": window_start + window
            }
        )
        return True

    @external
    def reset_window(self, caller: Address, account: Address) -> Bool:
        """
        Resets the limit window and cleared volume for a specific user.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)
        
        self.storage.set("win_vol:" + str(account), U128(0))
        self.storage.set("win_start:" + str(account), self.env.ledger().timestamp())
        
        self.env.emit_event("window_reset", {"account": account})
        return True

    @view
    def get_remaining_limit(self, account: Address) -> U128:
        """
        Returns the remaining volume allowed for the user in the current window.
        """
        self._require_initialized()
        limit, window = self._get_policy_for(account)
        current_time = self.env.ledger().timestamp()
        
        window_start = self.storage.get("win_start:" + str(account), U64(0))
        if current_time >= window_start + window:
            return limit
            
        current_volume = self.storage.get("win_vol:" + str(account), U128(0))
        if current_volume >= limit:
            return U128(0)
            
        return limit - current_volume

    @view
    def get_account_status(self, account: Address) -> Map:
        """
        Returns details of the user's limit policy, consumption, and window status.
        """
        self._require_initialized()
        limit, window = self._get_policy_for(account)
        current_time = self.env.ledger().timestamp()
        window_start = self.storage.get("win_start:" + str(account), U64(0))
        current_volume = self.storage.get("win_vol:" + str(account), U128(0))
        
        if current_time >= window_start + window:
            current_volume = U128(0)
            window_start = current_time
            
        status = Map()
        status.set(Symbol("limit"), limit)
        status.set(Symbol("window"), window)
        status.set(Symbol("current_volume"), current_volume)
        status.set(Symbol("window_start"), window_start)
        status.set(Symbol("window_end"), window_start + window)
        return status

    # Internal helpers
    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _get_policy_for(self, account: Address) -> (U128, U64):
        """
        Resolves custom limit, category limit, or default limit for a user.
        """
        # 1. Check custom overrides
        c_lim_key = "custom_limit:" + str(account)
        if self.storage.has(c_lim_key):
            return (
                self.storage.get(c_lim_key),
                self.storage.get("custom_window:" + str(account))
            )
            
        # 2. Check category policies
        cat_key = "acc_cat:" + str(account)
        if self.storage.has(cat_key):
            category = self.storage.get(cat_key)
            cat_lim_key = "cat_limit:" + str(category)
            if self.storage.has(cat_lim_key):
                return (
                    self.storage.get(cat_lim_key),
                    self.storage.get("cat_window:" + str(category))
                )
                
        # 3. Fallback to default
        return (
            self.storage.get("def_limit"),
            self.storage.get("def_window")
        )
