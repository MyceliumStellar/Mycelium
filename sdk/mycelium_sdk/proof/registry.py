"""
VerifierRegistryClient — drive the staked jury pool (`verifier_registry.py`).

Thin wrapper over `AgentContext.call_contract`, mirroring the on-chain externals.
A judge registers + stakes to become eligible for panels; the verification market
(the slasher) slashes outliers and records accuracy. See `PROOF_SYSTEM.md` §11.
"""

from decimal import Decimal
from typing import Any, Dict, Optional

from mycelium_sdk.scval import i128, u64

STROOPS_PER_XLM = 10_000_000


def _xlm_to_stroops(xlm) -> int:
    return int(Decimal(str(xlm)) * STROOPS_PER_XLM)


class VerifierRegistryClient:
    def __init__(self, context, registry_address: str):
        if not registry_address:
            raise ValueError("VerifierRegistryClient requires a deployed registry address.")
        self.context = context
        self.registry_address = registry_address

    def _call(self, fn: str, args, read_only: bool = False):
        return self.context.call_contract(
            contract_id=self.registry_address, function_name=fn, args=args, read_only=read_only)

    # ── admin ──────────────────────────────────────────────────────────────────
    def initialize(self, token: str, min_stake_xlm, unbond_secs: int, slasher: str):
        """One-time config: staking token, minimum bond, unbonding delay, and the
        slasher (the verification market). Signed by the admin."""
        return self._call("initialize", [
            self.context.keypair.public_key, token, i128(_xlm_to_stroops(min_stake_xlm)),
            u64(int(unbond_secs)), slasher])

    # ── judge ──────────────────────────────────────────────────────────────────
    def register(self, model_tags: str, endpoint: str = ""):
        """Announce judging capability (the model families this judge runs)."""
        return self._call("register", [
            self.context.keypair.public_key, model_tags.encode("utf-8"), endpoint.encode("utf-8")])

    def stake(self, amount_xlm):
        """Lock `amount_xlm` as a bond (adds to existing stake)."""
        return self._call("stake", [self.context.keypair.public_key, i128(_xlm_to_stroops(amount_xlm))])

    def request_unstake(self):
        """Begin the unbonding period before withdrawing."""
        return self._call("request_unstake", [self.context.keypair.public_key])

    def withdraw(self):
        """Reclaim the (possibly slashed) stake after unbonding."""
        return self._call("withdraw", [self.context.keypair.public_key])

    # ── market (slasher) ───────────────────────────────────────────────────────
    def slash(self, judge: str, amount_xlm, reason: str = "outlier"):
        """Cut a judge's stake (signed by the slasher/market)."""
        return self._call("slash", [judge, i128(_xlm_to_stroops(amount_xlm)), reason])

    def record_accuracy(self, judge: str, agreed: bool):
        """Record whether a judge's verdict tracked the panel median (verifier reputation)."""
        return self._call("record_accuracy", [judge, bool(agreed)])

    # ── reads ──────────────────────────────────────────────────────────────────
    def get(self, judge: str) -> Dict[str, Any]:
        """A judge's stake (XLM), active flag, model tags, and accuracy counters."""
        raw = self._call("get", [judge], read_only=True) or {}
        get = raw.get if isinstance(raw, dict) else (lambda *_: None)
        tags = get("tags")
        if isinstance(tags, (bytes, bytearray)):
            tags = tags.decode("utf-8", "replace")
        jobs = int(get("jobs") or 0)
        agreed = int(get("agreed") or 0)
        return {
            "stake_xlm": int(get("stake") or 0) / STROOPS_PER_XLM,
            "active": bool(get("active")),
            "tags": tags or "",
            "jobs": jobs,
            "agreed": agreed,
            "accuracy_bps": (agreed * 10000 // jobs) if jobs else 0,
            "unbond_at": int(get("unbond_at") or 0),
        }

    def is_eligible(self, judge: str) -> bool:
        """True if the judge is active and bonded at/above the minimum stake."""
        return bool(self._call("is_eligible", [judge], read_only=True))

    def min_stake_xlm(self) -> float:
        return int(self._call("min_stake", [], read_only=True) or 0) / STROOPS_PER_XLM
