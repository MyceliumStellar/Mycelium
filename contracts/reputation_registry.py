"""
ReputationRegistry — portable, on-chain agent reputation for the proof layer.

Reputation is the trust signal that makes agent-to-agent delegation work: before
agent B relies on agent A, it reads A's verified track record. The substrate is
the panel verdict — every job that passes verification has an on-chain `score`
(0..100) bound to the agent(s) that did the work (see `PROOF_SYSTEM.md` §12).

This is a small, dedicated contract (not buried inside one job board) so the
signal is reusable across boards and composable for A2A trust. An authorized
`recorder` — the job board / verification market at verdict time — calls `credit`
once per job; double-counting on retries is prevented by recording each job_id.

    python -m mycelium_compiler.main reputation_registry.py -o build/reputation_registry.wasm
"""

from mycelium import (
    contract, external, view,
    Address, U32, U64, Bool, Map, Env, Symbol,
)


class ContractError:
    ALREADY_INITIALIZED = 1
    NOT_INITIALIZED = 2
    NOT_RECORDER = 3
    ALREADY_CREDITED = 4


@contract
class ReputationRegistry:
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(self, admin: Address, recorder: Address) -> Bool:
        """Set the only address allowed to `credit` reputation — the job board /
        verification market that records verdicts. Configure once."""
        admin.require_auth()
        if self.storage.get("init", False):
            raise ContractError.ALREADY_INITIALIZED
        self.storage.set("admin", admin)
        self.storage.set("recorder", recorder)
        self.storage.set("init", True)
        return True

    @external
    def set_recorder(self, recorder: Address) -> Bool:
        """Admin rotates the authorized recorder (e.g. a new board version)."""
        admin = self.storage.get("admin")
        admin.require_auth()
        self.storage.set("recorder", recorder)
        return True

    @external
    def credit(self, agent: Address, job_id: U64, score: U32, passed: Bool) -> Bool:
        """
        Credit `agent` with the panel verdict for `job_id`: bump jobs_done, add the
        `score` to the running total, and bump jobs_passed on a pass. Only the
        authorized recorder may call. Idempotent per (agent, job_id) so a retried
        verdict can't inflate a score. For a swarm, the recorder calls this once
        per member — each collaborator earns the job's verified score.
        """
        if not self.storage.get("init", False):
            raise ContractError.NOT_INITIALIZED
        recorder = self.storage.get("recorder")
        recorder.require_auth()

        seen_key = ("seen", agent, job_id)
        if self.storage.get(seen_key, False):
            raise ContractError.ALREADY_CREDITED
        self.storage.set(seen_key, True)

        a = agent
        self.storage.set("jobs:" + a, self.storage.get("jobs:" + a, U32(0)) + U32(1))
        self.storage.set("sum:" + a, self.storage.get("sum:" + a, U32(0)) + score)
        if passed:
            self.storage.set("passed:" + a, self.storage.get("passed:" + a, U32(0)) + U32(1))
        self.storage.set("last:" + a, job_id)

        self.env.emit_event("reputation_credited", {"agent": agent, "job_id": job_id, "score": score, "passed": passed})
        return True

    @view
    def get(self, agent: Address) -> Map:
        """An agent's reputation: jobs_done, jobs_passed, cumulative score, last job.
        avg_score and pass_rate are derived off-chain from these (avoids on-chain
        division); `avg_score` is provided as a convenience integer."""
        a = agent
        jobs = self.storage.get("jobs:" + a, U32(0))
        total = self.storage.get("sum:" + a, U32(0))
        passed = self.storage.get("passed:" + a, U32(0))
        avg = U32(0)
        if jobs > U32(0):
            avg = total / jobs
        details = Map()
        details.set(Symbol("jobs_done"), jobs)
        details.set(Symbol("jobs_passed"), passed)
        details.set(Symbol("sum_score"), total)
        details.set(Symbol("avg_score"), avg)
        details.set(Symbol("last_job"), self.storage.get("last:" + a, U64(0)))
        return details
