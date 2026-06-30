"""
ReputationClient — read/write portable agent reputation (`reputation_registry.py`).

Reputation is credited from panel verdicts: the recorder (the judge/market) calls
`credit(agent, job_id, score, passed)` once per job; anyone can `get(agent)` to see
a worker's verified track record before trusting/hiring it (A2A). See
`PROOF_SYSTEM.md` §12.
"""

from typing import Any, Dict

from mycelium_sdk.scval import u32, u64


class ReputationClient:
    def __init__(self, context, registry_address: str):
        if not registry_address:
            raise ValueError("ReputationClient requires a deployed reputation registry address.")
        self.context = context
        self.registry_address = registry_address

    def initialize(self, recorder: str):
        """One-time: set the authorized recorder (the board/market). Signed by admin."""
        return self.context.call_contract(
            contract_id=self.registry_address, function_name="initialize",
            args=[self.context.keypair.public_key, recorder])

    def credit(self, agent: str, job_id: int, score: int, passed: bool):
        """Credit a worker with a job's verdict (signed by the recorder). Idempotent
        per (agent, job_id)."""
        return self.context.call_contract(
            contract_id=self.registry_address, function_name="credit",
            args=[agent, u64(job_id), u32(int(round(score))), bool(passed)])

    def get(self, agent: str) -> Dict[str, Any]:
        """An agent's reputation: jobs_done, jobs_passed, avg_score, pass_rate."""
        raw = self.context.call_contract(
            contract_id=self.registry_address, function_name="get",
            args=[agent], read_only=True) or {}
        g = raw.get if isinstance(raw, dict) else (lambda *_: None)
        jobs = int(g("jobs_done") or 0)
        passed = int(g("jobs_passed") or 0)
        return {
            "jobs_done": jobs,
            "jobs_passed": passed,
            "sum_score": int(g("sum_score") or 0),
            "avg_score": int(g("avg_score") or 0),
            "pass_rate_bps": (passed * 10000 // jobs) if jobs else 0,
            "last_job": int(g("last_job") or 0),
        }
