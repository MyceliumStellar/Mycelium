"""
JobBoardClient — Sovereign Job Boards: post tasks on-chain, have single agents
or multi-agent swarms claim, prove, and split the bounty.

Thin wrapper over `AgentContext.call_contract` against the deployed `JobBoard`
contract (`job_board_contract.py`), mirroring the on-chain externals. The bounty
itself is locked in an `Escrow` instance (`escrow_contract.py`) created at post
time; `finalize` releases it — a single payout, or an N-way swarm split via
`EscrowPaymentRouter.split_release`. Coordination (who joins a swarm, with what
share) happens off-chain via the A2A / Hive Registry primitives, then is recorded
here with `join_swarm`.

There is no mocking: every method is a real Soroban call. Reads (`list_open_jobs`,
`get_job`) are read-only simulations; the rest sign + submit.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from mycelium_sdk.scval import u32, u64
from mycelium_sdk.x402.settlement import EscrowPaymentRouter, STROOPS_PER_XLM, DEFAULT_ESCROW_TIMEOUT_SECONDS

# Job lifecycle status symbols emitted/stored by the contract.
STATUS_OPEN = "open"
STATUS_CLAIMED = "claimed"
STATUS_SUBMITTED = "submitted"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"

# Event topic the JobBoard publishes on every post (used by `list_open_jobs`
# discovery, mirroring Hive Registry's `agent_registered` scan).
JOB_POSTED_TOPIC = "job_posted"
_EVENT_PAGE_LIMIT = 100
_LEDGER_WINDOW = 16000
_MAX_WINDOWS = 64


def _addr_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    return getattr(value, "address", None) or str(value)


def _bytes_to_str(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value
    return value


class JobBoardClient:
    def __init__(self, context, board_address: str):
        if not board_address:
            raise ValueError(
                "JobBoardClient requires a deployed JobBoard contract address "
                "(set [jobs].board_address in mycelium.toml or pass it explicitly)."
            )
        self.context = context
        self.board_address = board_address

    # ── posting ──────────────────────────────────────────────────────────────
    def post_job(
        self,
        spec_uri: str,
        spec_hash: bytes,
        bounty_xlm: Decimal,
        mode: str = "single",
        token: Optional[str] = None,
        deadline_seconds: int = DEFAULT_ESCROW_TIMEOUT_SECONDS,
    ) -> int:
        """
        Lock `bounty_xlm` into a fresh escrow and record a new job, returning its
        `job_id`. `spec_hash` is the SHA-256 of the task spec (the proof must hash
        to it). `mode` is "single" or "swarm".
        """
        if mode not in ("single", "swarm"):
            raise ValueError("mode must be 'single' or 'swarm'.")

        from mycelium_sdk.constants import native_token_address

        token = token or native_token_address(self.context.network_type)
        bounty_stroops = int(Decimal(str(bounty_xlm)) * STROOPS_PER_XLM)
        poster = self.context.keypair.public_key

        # Lock the bounty. The placeholder provider is the poster; the real
        # recipients are decided at finalize via claim_funds / split_release.
        escrow_id = EscrowPaymentRouter(self.context).create_locked_escrow(
            provider_id=poster,
            amount_xlm=Decimal(str(bounty_xlm)),
            task_hash=spec_hash,
            token=token,
            timeout_seconds=deadline_seconds,
        )

        result = self.context.call_contract(
            contract_id=self.board_address,
            function_name="post_job",
            args=[
                poster,
                spec_uri.encode("utf-8"),
                spec_hash,
                bounty_stroops,
                token,
                mode,  # short alnum -> Soroban Symbol
                escrow_id,
                u64(deadline_seconds),
            ],
        )
        job_id = getattr(result, "return_value", result)
        return int(job_id)

    # ── claiming ─────────────────────────────────────────────────────────────
    def claim_job(self, job_id: int):
        """Single-mode self-claim of an open job."""
        return self.context.call_contract(
            contract_id=self.board_address,
            function_name="claim_job",
            args=[self.context.keypair.public_key, u64(job_id)],
        )

    def assign_agent(self, job_id: int, agent: str):
        """Poster-side assignment of `agent` (a G/C address) to an open job."""
        return self.context.call_contract(
            contract_id=self.board_address,
            function_name="assign_agent",
            args=[u64(job_id), agent],
        )

    def join_swarm(self, job_id: int, capability_tag: str, share_bps: int):
        """Join a swarm job with an agreed bounty share (basis points)."""
        return self.context.call_contract(
            contract_id=self.board_address,
            function_name="join_swarm",
            args=[
                self.context.keypair.public_key,
                u64(job_id),
                capability_tag.encode("utf-8"),
                u32(share_bps),
            ],
        )

    # ── completion ───────────────────────────────────────────────────────────
    def submit_proof(self, job_id: int, proof: bytes):
        """Record a completion proof whose SHA-256 matches the job's spec_hash."""
        return self.context.call_contract(
            contract_id=self.board_address,
            function_name="submit_proof",
            args=[u64(job_id), proof],
        )

    def finalize(self, job_id: int, proof: bytes):
        """
        Release the bounty and mark the job done. For a single-agent job the full
        bounty goes to the claimant; for a swarm it is split across members per
        their recorded shares (`EscrowPaymentRouter.split_release`). `proof` must
        SHA-256 to the job's spec_hash.
        """
        job = self.get_job(job_id)
        escrow_id = job["escrow"]
        router = EscrowPaymentRouter(self.context)

        if job.get("mode") == "swarm":
            members = self.get_swarm(job_id)
            shares = self.get_shares(job_id)
            if not members:
                raise RuntimeError(f"Job {job_id} is a swarm with no members to pay.")
            pairs: List[Tuple[str, int]] = [
                (_addr_str(m), int(s)) for m, s in zip(members, shares)
            ]
            router.split_release(escrow_id, pairs, proof)
        else:
            # Single payout: pay the claimant the whole bounty (1-way split keeps
            # one release path and lets the escrow re-check the balance).
            router.split_release(escrow_id, [(job["agent"], 10000)], proof)

        return self.context.call_contract(
            contract_id=self.board_address,
            function_name="finalize",
            args=[u64(job_id)],
        )

    def cancel_job(self, job_id: int):
        """Poster cancels an unclaimed job."""
        return self.context.call_contract(
            contract_id=self.board_address,
            function_name="cancel_job",
            args=[u64(job_id)],
        )

    # ── reads (simulated, no fee) ────────────────────────────────────────────
    def get_job(self, job_id: int) -> Dict[str, Any]:
        """Return a job's current state (poster, bounty, mode, escrow, status, …)."""
        raw = self.context.call_contract(
            contract_id=self.board_address,
            function_name="get_job",
            args=[u64(job_id)],
            read_only=True,
        )
        return self._parse_job(raw, job_id)

    def get_swarm(self, job_id: int) -> List[str]:
        """Return the swarm member addresses recorded for a job."""
        raw = self.context.call_contract(
            contract_id=self.board_address,
            function_name="get_swarm",
            args=[u64(job_id)],
            read_only=True,
        )
        return [_addr_str(m) for m in (raw or [])]

    def get_shares(self, job_id: int) -> List[int]:
        """Return the swarm members' bounty shares (bps), index-aligned with get_swarm."""
        raw = self.context.call_contract(
            contract_id=self.board_address,
            function_name="get_shares",
            args=[u64(job_id)],
            read_only=True,
        )
        return [int(s) for s in (raw or [])]

    def job_count(self) -> int:
        """Return the number of jobs posted so far (ids run 1..job_count)."""
        raw = self.context.call_contract(
            contract_id=self.board_address,
            function_name="job_count",
            args=[],
            read_only=True,
        )
        return int(raw or 0)

    def list_open_jobs(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Enumerate jobs 1..job_count via read-only `get_job` calls, optionally
        filtered by `status` (e.g. "open"). Returns newest-first.

        Walking the counter is O(n) simulations; an off-chain indexer (roadmap
        M4) replaces this for scale, but it is fee-free and exact for now.
        """
        count = self.job_count()
        jobs: List[Dict[str, Any]] = []
        for job_id in range(count, 0, -1):
            try:
                job = self.get_job(job_id)
            except Exception:
                continue
            if status is None or job.get("status") == status:
                jobs.append(job)
        return jobs

    # ── parsing ──────────────────────────────────────────────────────────────
    @staticmethod
    def _parse_job(raw: Any, job_id: int) -> Dict[str, Any]:
        if raw is None:
            raise KeyError(f"Job {job_id} not found.")
        if isinstance(raw, dict):
            get = raw.get
        else:
            raise TypeError(f"Unexpected get_job return type: {type(raw).__name__}")
        bounty = get("bounty")
        deadline = get("deadline")
        return {
            "job_id": job_id,
            "poster": _addr_str(get("poster")),
            "bounty_stroops": int(bounty) if bounty is not None else 0,
            "token": _addr_str(get("token")),
            "mode": str(get("mode")) if get("mode") is not None else None,
            "escrow": _addr_str(get("escrow")),
            "deadline": int(deadline) if deadline is not None else 0,
            "status": str(get("status")) if get("status") is not None else None,
            "agent": _addr_str(get("agent")),
        }
