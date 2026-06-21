"""
Oracle Registry — Directory of oracle providers, stakes, query pricing, reputation, and SLA monitoring.

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
    PROVIDER_ALREADY_REGISTERED = 4
    PROVIDER_NOT_REGISTERED = 5
    INSUFFICIENT_STAKE = 6
    COOLDOWN_ACTIVE = 7
    NO_WITHDRAWAL_REQUESTED = 8
    TRANSFER_FAILED = 9
    REENTRANT_CALL = 10
    MAX_CATEGORIES_EXCEEDED = 11


# Limits
MAX_CATEGORIES = 5
INITIAL_REPUTATION = 1000
MAX_REPUTATION = 10000


@contract
class OracleRegistry:
    """Oracle Registry managing oracle node listings, dynamic category indexing,
    staking requirements, SLA performance logging, and reputation tracking."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        stake_token: Address,
        min_stake: U128,
        unregister_cooldown: U64,
    ):
        """Initialize configurations."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("stake_token", stake_token)
        self.storage.set("min_stake", min_stake)
        self.storage.set("unregister_cooldown", unregister_cooldown)
        self.storage.set("provider_count", U64(0))
        
        self.storage.set("execution_lock", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "stake_token": stake_token,
            "min_stake": min_stake,
        })

    # ------------------------------------------------------------------ #
    #  Provider Actions                                                   #
    # ------------------------------------------------------------------ #

    @external
    def register_provider(
        self,
        provider: Address,
        categories: Vec,
        stake_amount: U128,
        query_price: U128,
    ):
        """Register a new oracle provider and deposit stake.

        Args:
            provider: Address of the provider.
            categories: List of categories (e.g. Symbol("price"), Symbol("weather")).
            stake_amount: Stake tokens to deposit (must be >= min_stake).
            query_price: Cost in tokens per oracle query.
        """
        self._require_initialized()
        provider.require_auth()

        if len(categories) == 0 or len(categories) > MAX_CATEGORIES:
            raise ContractError.MAX_CATEGORIES_EXCEEDED

        # Check if already registered
        existing = self.storage.get(("provider", provider), None)
        if existing is not None and existing["active"]:
            raise ContractError.PROVIDER_ALREADY_REGISTERED

        min_stake = self.storage.get("min_stake")
        if stake_amount < min_stake:
            raise ContractError.INSUFFICIENT_STAKE

        stake_token = self.storage.get("stake_token")
        contract_addr = self.env.current_contract_address()
        success = self.env.invoke_contract(stake_token, "transfer", [provider, contract_addr, stake_amount])
        if not success:
            raise ContractError.TRANSFER_FAILED

        provider_data = {
            "provider": provider,
            "categories": categories,
            "stake": stake_amount,
            "query_price": query_price,
            "reputation": U64(INITIAL_REPUTATION),
            "successful_responses": U64(0),
            "total_requests": U64(0),
            "active": True,
            "unregister_requested_at": U64(0),
        }

        self.storage.set(("provider", provider), provider_data)

        # Update categories listing
        for i in range(len(categories)):
            cat = categories[i]
            self._add_to_category(cat, provider)

        p_count = self.storage.get("provider_count", U64(0)) + U64(1)
        self.storage.set("provider_count", p_count)

        self.env.emit_event("provider_registered", {
            "provider": provider,
            "stake": stake_amount,
            "query_price": query_price,
        })

    @external
    def update_pricing(self, provider: Address, new_price: U128):
        """Update the query pricing fee.

        Args:
            provider: Registered provider address.
            new_price: New cost per query.
        """
        self._require_initialized()
        provider.require_auth()

        p_data = self._get_provider(provider)
        if not p_data["active"]:
            raise ContractError.PROVIDER_NOT_REGISTERED

        p_data["query_price"] = new_price
        self.storage.set(("provider", provider), p_data)

        self.env.emit_event("pricing_updated", {"provider": provider, "new_price": new_price})

    @external
    def update_categories(self, provider: Address, new_categories: Vec):
        """Update supported categories.

        Args:
            provider: Provider address.
            new_categories: Vector of new categories.
        """
        self._require_initialized()
        provider.require_auth()

        if len(new_categories) == 0 or len(new_categories) > MAX_CATEGORIES:
            raise ContractError.MAX_CATEGORIES_EXCEEDED

        p_data = self._get_provider(provider)
        if not p_data["active"]:
            raise ContractError.PROVIDER_NOT_REGISTERED

        old_categories = p_data["categories"]

        # Clean old categories associations
        for i in range(len(old_categories)):
            self._remove_from_category(old_categories[i], provider)

        # Register new categories
        for i in range(len(new_categories)):
            self._add_to_category(new_categories[i], provider)

        p_data["categories"] = new_categories
        self.storage.set(("provider", provider), p_data)

        self.env.emit_event("categories_updated", {"provider": provider, "categories": new_categories})

    @external
    def request_unregister(self, provider: Address):
        """Initiate provider unregistration. Starts a cooldown before stake is releasable."""
        self._require_initialized()
        provider.require_auth()

        p_data = self._get_provider(provider)
        if not p_data["active"]:
            raise ContractError.PROVIDER_NOT_REGISTERED

        now = self.env.ledger().timestamp()
        p_data["unregister_requested_at"] = now
        p_data["active"] = False

        self.storage.set(("provider", provider), p_data)

        # Remove from category listings immediately
        cats = p_data["categories"]
        for i in range(len(cats)):
            self._remove_from_category(cats[i], provider)

        self.env.emit_event("unregister_requested", {"provider": provider, "requested_at": now})

    @external
    def withdraw_stake(self, provider: Address):
        """Claim stakes after unregistration cooldown has elapsed."""
        self._require_initialized()
        provider.require_auth()
        self._require_no_reentrant()

        p_data = self._get_provider(provider)
        if p_data["active"]:
            raise ContractError.INVALID_STATE
        if p_data["unregister_requested_at"] == U64(0):
            raise ContractError.NO_WITHDRAWAL_REQUESTED

        now = self.env.ledger().timestamp()
        cooldown = self.storage.get("unregister_cooldown")
        if now < p_data["unregister_requested_at"] + cooldown:
            raise ContractError.COOLDOWN_ACTIVE

        stake = p_data["stake"]
        if stake == U128(0):
            raise ContractError.INSUFFICIENT_STAKE

        p_data["stake"] = U128(0)
        p_data["unregister_requested_at"] = U64(0)
        self.storage.set(("provider", provider), p_data)

        # Transfer back stake
        stake_token = self.storage.get("stake_token")
        contract_addr = self.env.current_contract_address()
        success = self.env.invoke_contract(stake_token, "transfer", [contract_addr, provider, stake])
        if not success:
            raise ContractError.TRANSFER_FAILED

        self.env.emit_event("stake_reclaimed", {"provider": provider, "stake": stake})

    # ------------------------------------------------------------------ #
    #  SLA Monitoring & Reputation                                       #
    # ------------------------------------------------------------------ #

    @external
    def report_performance(self, reporter: Address, provider: Address, success: Bool):
        """Submit SLA success/failure update. Only admin or authorized clients.

        Args:
            reporter: Submitting client contract or Admin.
            provider: Evaluated provider.
            success: True if provider responded correctly and on-time, False if timeout or error.
        """
        self._require_initialized()
        reporter.require_auth()
        self._require_admin_or_client(reporter)

        p_data = self._get_provider(provider)
        p_data["total_requests"] = p_data["total_requests"] + U64(1)

        rep = p_data["reputation"]

        if success:
            p_data["successful_responses"] = p_data["successful_responses"] + U64(1)
            # Increase reputation by 5 points up to MAX_REPUTATION
            if rep + U64(5) <= U64(MAX_REPUTATION):
                p_data["reputation"] = rep + U64(5)
            else:
                p_data["reputation"] = U64(MAX_REPUTATION)
        else:
            # Decrease reputation by 100 points
            if rep >= U64(100):
                p_data["reputation"] = rep - U64(100)
            else:
                p_data["reputation"] = U64(0)

            # Slashed condition: If reputation drops below 500, slash 10% of their stake
            if p_data["reputation"] < U64(500) and p_data["stake"] > U128(0):
                slash_amt = p_data["stake"] / U128(10)
                p_data["stake"] = p_data["stake"] - slash_amt
                
                self.env.emit_event("provider_slashed", {
                    "provider": provider,
                    "slashed_amount": slash_amt,
                    "remaining_stake": p_data["stake"],
                })

        self.storage.set(("provider", provider), p_data)

        self.env.emit_event("sla_reported", {
            "provider": provider,
            "success": success,
            "new_reputation": p_data["reputation"],
        })

    # ------------------------------------------------------------------ #
    #  Admin Configurations                                               #
    # ------------------------------------------------------------------ #

    @external
    def add_client_contract(self, admin: Address, client: Address):
        """Authorize a client contract to log SLA performance. Only Admin."""
        self._require_admin(admin)
        self.storage.set(("client", client), True)
        self.env.emit_event("client_added", {"client": client})

    @external
    def remove_client_contract(self, admin: Address, client: Address):
        """Deauthorize a client contract. Only Admin."""
        self._require_admin(admin)
        self.storage.set(("client", client), False)
        self.env.emit_event("client_removed", {"client": client})

    @external
    def update_config(self, admin: Address, min_stake: U128, unregister_cooldown: U64):
        """Update configuration settings. Only Admin."""
        self._require_admin(admin)
        self.storage.set("min_stake", min_stake)
        self.storage.set("unregister_cooldown", unregister_cooldown)
        self.env.emit_event("config_updated", {"min_stake": min_stake, "cooldown": unregister_cooldown})

    @external
    def transfer_admin(self, admin: Address, new_admin: Address):
        """Transfer admin role. Only Admin."""
        self._require_admin(admin)
        self.storage.set("admin", new_admin)
        self.env.emit_event("admin_transferred", {"old_admin": admin, "new_admin": new_admin})

    # ------------------------------------------------------------------ #
    #  View & Discovery Functions                                         #
    # ------------------------------------------------------------------ #

    @view
    def get_provider_details(self, provider: Address) -> Map:
        """Get provider registry details."""
        self._require_initialized()
        return self._get_provider(provider)

    @view
    def get_category_providers(self, category: Symbol) -> Vec:
        """Get list of provider addresses in a category."""
        self._require_initialized()
        return self.storage.get(("cat_providers", category), Vec())

    @view
    def select_best_provider(self, category: Symbol) -> Address:
        """Find the active provider with highest reputation and lowest pricing in category."""
        self._require_initialized()
        providers = self.storage.get(("cat_providers", category), Vec())
        if len(providers) == 0:
            raise ContractError.PROVIDER_NOT_REGISTERED

        best_provider = providers[0]
        # In Stellar/Mycelium, we initialize lookup variables
        best_p_data = self._get_provider(best_provider)
        best_score = best_p_data["reputation"]
        best_price = best_p_data["query_price"]

        for i in range(1, len(providers)):
            p = providers[i]
            p_data = self._get_provider(p)
            if not p_data["active"]:
                continue
            
            # Selection metric: highest reputation, tie-break by lowest query price
            if p_data["reputation"] > best_score:
                best_provider = p
                best_score = p_data["reputation"]
                best_price = p_data["query_price"]
            elif p_data["reputation"] == best_score and p_data["query_price"] < best_price:
                best_provider = p
                best_price = p_data["query_price"]

        return best_provider

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

    def _require_admin_or_client(self, caller: Address):
        admin = self.storage.get("admin")
        if caller == admin:
            return
        is_client = self.storage.get(("client", caller), False)
        if not is_client:
            raise ContractError.UNAUTHORIZED

    def _require_no_reentrant(self):
        if self.storage.get("execution_lock", False):
            raise ContractError.REENTRANT_CALL

    def _get_provider(self, provider: Address) -> Map:
        data = self.storage.get(("provider", provider), None)
        if data is None:
            raise ContractError.PROVIDER_NOT_REGISTERED
        return data

    def _add_to_category(self, category: Symbol, provider: Address):
        providers = self.storage.get(("cat_providers", category), Vec())
        # Check if already in vector
        exists = False
        for i in range(len(providers)):
            if providers[i] == provider:
                exists = True
                break
        if not exists:
            providers.append(provider)
            self.storage.set(("cat_providers", category), providers)

    def _remove_from_category(self, category: Symbol, provider: Address):
        providers = self.storage.get(("cat_providers", category), Vec())
        new_vec = Vec()
        for i in range(len(providers)):
            if providers[i] != provider:
                new_vec.append(providers[i])
        self.storage.set(("cat_providers", category), new_vec)
