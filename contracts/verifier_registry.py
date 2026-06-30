"""
VerifierRegistry — the staked jury pool for the Mycelium proof layer (P2).

This is the keystone of trustless verification (see `PROOF_SYSTEM.md` §11). Today a
single trusted key runs the judge panel; P2 replaces that with **independent judges
who put money behind their honesty**. A judge registers its model capability, locks
an XLM stake, and becomes eligible to be drawn onto panels. The verification market
(the only authorized `slasher`) rewards judges whose verdicts track the panel median
and **slashes** the stake of outliers and no-shows — so the honest read is the
profitable play.

The registry also tracks each judge's **accuracy** (votes cast vs. votes within
tolerance of the median) — verifier reputation, the counterpart to worker reputation
(§12). Posters can then prefer high-accuracy judges.

Authored in the Mycelium DSL and compiled with this repo's own compiler:

    python -m mycelium_compiler.main verifier_registry.py -o build/verifier_registry.wasm

Staking/slashing move real value via the Soroban token interface, so the bond is a
genuine economic commitment, not a flag.
"""

from mycelium import (
    contract, external, view,
    Address, U32, U64, I128, Bytes, Bool, Map, Env, Symbol,
)


class ContractError:
    ALREADY_INITIALIZED = 1
    NOT_INITIALIZED = 2
    NOT_REGISTERED = 3
    INSUFFICIENT_STAKE = 4
    NOT_SLASHER = 5
    UNBONDING = 6
    NOT_EXPIRED = 7
    BAD_AMOUNT = 8


@contract
class VerifierRegistry:
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, token: Address, min_stake: I128, unbond_secs: U64, slasher: Address) -> Bool:
        """
        Configure the registry once: `token` is the staking asset, `min_stake` the
        bond required to be eligible to judge, `unbond_secs` the delay between
        requesting an unstake and withdrawing (so a judge can't dodge a pending
        slash), and `slasher` the only address allowed to slash / record accuracy
        (the verification market).
        """
        admin.require_auth()
        if self.storage.get("init", False):
            raise ContractError.ALREADY_INITIALIZED
        self.storage.set("admin", admin)
        self.storage.set("token", token)
        self.storage.set("min_stake", min_stake)
        self.storage.set("unbond_secs", unbond_secs)
        self.storage.set("slasher", slasher)
        self.storage.set("init", True)
        return True

    @external
    def register(self, judge: Address, model_tags: Bytes, endpoint: Bytes) -> Bool:
        """Announce judging capability: `model_tags` (the model families this judge
        runs, e.g. b"nvidia:deepseek,groq:llama") and an optional `endpoint`."""
        if not self.storage.get("init", False):
            raise ContractError.NOT_INITIALIZED
        judge.require_auth()
        self.storage.set("tags:" + judge, model_tags)
        self.storage.set("endpoint:" + judge, endpoint)
        self.storage.set("active:" + judge, True)
        self.env.emit_event("verifier_registered", {"judge": judge})
        return True

    @external
    def stake(self, judge: Address, amount: I128) -> Bool:
        """Lock `amount` of the staking token as a bond. Adds to any existing stake.
        Cancels a pending unstake (re-committing means you're not leaving)."""
        if not self.storage.get("init", False):
            raise ContractError.NOT_INITIALIZED
        judge.require_auth()
        if amount <= I128(0):
            raise ContractError.BAD_AMOUNT

        token = self.storage.get("token")
        self.env.transfer(judge, self.env.current_contract_address(), token, amount)

        key = "stake:" + judge
        self.storage.set(key, self.storage.get(key, I128(0)) + amount)
        self.storage.set("unbond_at:" + judge, U64(0))  # cancel any unstake
        self.env.emit_event("verifier_staked", {"judge": judge, "amount": amount})
        return True

    @external
    def request_unstake(self, judge: Address) -> Bool:
        """Begin the unbonding period; `withdraw` becomes available after it."""
        if not self.storage.get("init", False):
            raise ContractError.NOT_INITIALIZED
        judge.require_auth()
        deadline = self.env.ledger().timestamp() + self.storage.get("unbond_secs", U64(0))
        self.storage.set("unbond_at:" + judge, deadline)
        self.storage.set("active:" + judge, False)
        self.env.emit_event("verifier_unstaking", {"judge": judge})
        return True

    @external
    def withdraw(self, judge: Address) -> Bool:
        """Return the (possibly slashed) stake after the unbonding period elapses."""
        if not self.storage.get("init", False):
            raise ContractError.NOT_INITIALIZED
        judge.require_auth()
        unbond_at = self.storage.get("unbond_at:" + judge, U64(0))
        if unbond_at == U64(0):
            raise ContractError.UNBONDING
        if self.env.ledger().timestamp() < unbond_at:
            raise ContractError.NOT_EXPIRED

        key = "stake:" + judge
        amount = self.storage.get(key, I128(0))
        if amount > I128(0):
            token = self.storage.get("token")
            self.env.transfer(self.env.current_contract_address(), judge, token, amount)
        self.storage.set(key, I128(0))
        self.storage.set("unbond_at:" + judge, U64(0))
        self.env.emit_event("verifier_withdrew", {"judge": judge, "amount": amount})
        return True

    @external
    def slash(self, judge: Address, amount: I128, reason: Symbol) -> Bool:
        """
        Cut `amount` from a judge's stake — only the configured `slasher` (the
        verification market) may call this, on an outlier or no-show verdict. The
        slashed amount is sent to the slasher (treasury / honest-judge reward pool).
        """
        if not self.storage.get("init", False):
            raise ContractError.NOT_INITIALIZED
        slasher = self.storage.get("slasher")
        slasher.require_auth()

        key = "stake:" + judge
        current = self.storage.get(key, I128(0))
        cut = amount
        if cut > current:
            cut = current
        self.storage.set(key, current - cut)
        if cut > I128(0):
            token = self.storage.get("token")
            self.env.transfer(self.env.current_contract_address(), slasher, token, cut)
        self.env.emit_event("verifier_slashed", {"judge": judge, "amount": cut, "reason": reason})
        return True

    @external
    def record_accuracy(self, judge: Address, agreed: Bool) -> Bool:
        """
        Verifier reputation: the market records, per judged job, whether this
        judge's revealed score landed within tolerance of the panel median. Only
        the slasher/market may call. `jobs` counts votes; `agreed` counts the
        in-tolerance ones — accuracy = agreed / jobs.
        """
        if not self.storage.get("init", False):
            raise ContractError.NOT_INITIALIZED
        slasher = self.storage.get("slasher")
        slasher.require_auth()

        jkey = "jobs:" + judge
        self.storage.set(jkey, self.storage.get(jkey, U32(0)) + U32(1))
        if agreed:
            akey = "agreed:" + judge
            self.storage.set(akey, self.storage.get(akey, U32(0)) + U32(1))
        return True

    @view
    def get(self, judge: Address) -> Map:
        """A judge's stake + activity for off-chain inspection / panel selection."""
        details = Map()
        details.set(Symbol("stake"), self.storage.get("stake:" + judge, I128(0)))
        details.set(Symbol("active"), self.storage.get("active:" + judge, False))
        details.set(Symbol("tags"), self.storage.get("tags:" + judge, Bytes(b"")))
        details.set(Symbol("jobs"), self.storage.get("jobs:" + judge, U32(0)))
        details.set(Symbol("agreed"), self.storage.get("agreed:" + judge, U32(0)))
        details.set(Symbol("unbond_at"), self.storage.get("unbond_at:" + judge, U64(0)))
        return details

    @view
    def is_eligible(self, judge: Address) -> Bool:
        """True if the judge is active and bonded at/above the minimum stake."""
        active = self.storage.get("active:" + judge, False)
        staked = self.storage.get("stake:" + judge, I128(0))
        return active and staked >= self.storage.get("min_stake", I128(0))

    @view
    def min_stake(self) -> I128:
        """The bond required to be eligible to judge."""
        return self.storage.get("min_stake", I128(0))
