"""
JobBoardClient — Sovereign Job Boards: post tasks on-chain, have single agents
or multi-agent swarms claim, deliver, get judged, and split the bounty.

Thin wrapper over `AgentContext.call_contract` against the deployed `JobBoard`
contract (`job_board_contract.py`), mirroring the on-chain externals. The bounty
is locked in an `Escrow` instance (`escrow_contract.py`) created at post time and
released by the job's `judge` on a passing verdict — a single payout, or an N-way
swarm split via `EscrowPaymentRouter.split_release`.

The lifecycle is the proof layer (see `PROOF_SYSTEM.md`):
`post_job` (rubric + judge) → `claim_job` / `join_swarm` → `submit_evidence`
(anchor the deliverable, not the spec) → `record_verdict` + `release_bounty`
(judge) → `finalize` (poster closes the record). The old `submit_proof` /
hash-to-spec gate is gone: a submission is judged on its merits.

There is no mocking: every method is a real Soroban call. Reads (`list_open_jobs`,
`get_job`) are read-only simulations; the rest sign + submit.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from mycelium_sdk.scval import u32, u64
from mycelium_sdk.x402.settlement import EscrowPaymentRouter, STROOPS_PER_XLM, DEFAULT_ESCROW_TIMEOUT_SECONDS
from mycelium_sdk.logging import get_logger

_log = get_logger("jobs")

# Job lifecycle status symbols emitted/stored by the contract.
STATUS_OPEN = "open"
STATUS_CLAIMED = "claimed"
STATUS_SUBMITTED = "submitted"
STATUS_VERIFIED = "verified"
STATUS_REJECTED = "rejected"
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
    def post_bounty(
        self,
        title: str,
        description: str,
        checks,
        judge_models,
        bounty_xlm: Decimal,
        judge: str,
        *,
        pass_threshold: int = 70,
        deliverable_type: str = "any",
        mode: str = "single",
        token: Optional[str] = None,
        deadline_seconds: int = DEFAULT_ESCROW_TIMEOUT_SECONDS,
    ) -> int:
        """
        Post a fully self-describing bounty for ANY kind of job. Required inputs
        are exactly what a poster must decide:

          - ``title``        — the job heading (shown on the bounty page)
          - ``description``  — what the work is
          - ``checks``       — the acceptance checks: a list of ``(id, text, weight)``
                               tuples or ``{"id","check","weight"[,"type"]}`` dicts;
                               LLM-judged by default
          - ``judge_models`` — the poster's chosen panel: ``["provider:model", ...]``
                               (e.g. ``["nvidia:deepseek-ai/deepseek-v4-pro",
                               "groq:llama-3.3-70b-versatile"]``)
          - ``pass_threshold`` — payout only if the panel's weighted score ≥ this

        These are built into a canonical ``Rubric``, whose JSON ``spec`` and SHA-256
        are stored ON-CHAIN (title + description + spec), so the whole bounty is
        readable straight from the contract via ``get_job`` with no off-chain
        dependency. Returns the new ``job_id``.
        """
        from mycelium_sdk.proof.rubric import Rubric, Criterion, CriterionType

        criteria = []
        for c in checks:
            if isinstance(c, dict):
                criteria.append(Criterion(c["id"], c["check"], int(c["weight"]),
                                          c.get("type", CriterionType.LLM)))
            else:
                cid, text, weight = c
                criteria.append(Criterion(cid, text, int(weight), CriterionType.LLM))

        rubric = Rubric(
            job=description, title=title, criteria=criteria,
            pass_threshold=pass_threshold, deliverable_type=deliverable_type,
            judge_models=list(judge_models),
        )
        return self.post_job(rubric, bounty_xlm, judge, mode=mode, token=token,
                             deadline_seconds=deadline_seconds)

    def post_job(
        self,
        rubric,
        bounty_xlm: Decimal,
        judge: str,
        mode: str = "single",
        token: Optional[str] = None,
        deadline_seconds: int = DEFAULT_ESCROW_TIMEOUT_SECONDS,
    ) -> int:
        """
        Lower-level post: lock `bounty_xlm` into a fresh judge-gated escrow and
        record a new job from a built `Rubric`, returning its `job_id`. The
        rubric's `title`, `description`, and canonical `spec` (checks + judge
        panel) are stored on-chain; `rubric_hash` anchors integrity. `judge` is
        the verdict authority that gates release (the same address on the escrow).
        Most callers should use `post_bounty`.
        """
        if mode not in ("single", "swarm"):
            raise ValueError("mode must be 'single' or 'swarm'.")
        if not judge:
            raise ValueError("post_job requires a judge address (the verdict authority).")
        if not getattr(rubric, "judge_models", None):
            raise ValueError("Rubric names no judge models; the poster must choose a judge panel.")

        bounty = Decimal(str(bounty_xlm))
        if bounty <= 0:
            raise ValueError(f"Job bounty must be positive (got {bounty_xlm} XLM).")
        if deadline_seconds <= 0:
            raise ValueError(f"Job deadline must be positive (got {deadline_seconds}s).")

        from mycelium_sdk.constants import native_token_address

        token = token or native_token_address(self.context.network_type)
        bounty_stroops = int(bounty * STROOPS_PER_XLM)
        if bounty_stroops <= 0:
            raise ValueError(
                f"Job bounty {bounty_xlm} XLM rounds to 0 stroops; use at least "
                f"0.0000001 XLM (1 stroop)."
            )
        poster = self.context.keypair.public_key
        spec = rubric.canonical_json()
        rubric_hash = rubric.rubric_hash()

        # Lock the bounty. The placeholder provider is the poster; the real
        # recipients are named by the judge at release (claim_funds / split_release).
        escrow_id = EscrowPaymentRouter(self.context).create_locked_escrow(
            provider_id=poster,
            amount_xlm=Decimal(str(bounty_xlm)),
            judge=judge,
            token=token,
            timeout_seconds=deadline_seconds,
        )

        result = self.context.call_contract(
            contract_id=self.board_address,
            function_name="post_job",
            args=[
                poster,
                rubric.title.encode("utf-8"),
                rubric.description.encode("utf-8"),
                spec,
                rubric_hash,
                bounty_stroops,
                token,
                mode,  # short alnum -> Soroban Symbol
                escrow_id,
                judge,
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
        # Contract only checks the upper bound; reject non-positive / >100% here
        # so a bad share fails before it ever costs a transaction.
        if not 0 < int(share_bps) <= 10000:
            raise ValueError(
                f"share_bps must be between 1 and 10000 basis points (got {share_bps})."
            )
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
    def submit_evidence(self, job_id: int, evidence_root: bytes, evidence_uri: str = ""):
        """
        Anchor the claimant's evidence on-chain: the 32-byte `evidence_root`
        (`mycelium_sdk.proof.EvidenceBundle.evidence_root()`) plus `evidence_uri`,
        the pointer to where the full bundle can be fetched and verified against
        the root. Signed by the wallet keypair, which must be the recorded
        claimant (single-mode agent or swarm member) — the contract enforces this.

        Replaces `submit_proof`: no hash-to-spec check. A submission is a real
        deliverable, judged on its merits, not the spec echoed back.
        """
        if len(evidence_root) != 32:
            raise ValueError(
                f"evidence_root must be 32 bytes (got {len(evidence_root)}); "
                "pass EvidenceBundle.evidence_root()."
            )
        return self.context.call_contract(
            contract_id=self.board_address,
            function_name="submit_evidence",
            args=[self.context.keypair.public_key, u64(job_id), evidence_root,
                  evidence_uri.encode("utf-8")],
        )

    def record_verdict(self, job_id: int, passed: bool, score: int, evidence_root: bytes):
        """
        Record the panel's verdict on-chain — the pass/fail AND the numeric
        `score` (0..100 weighted aggregate, for audit + reputation). Signed by the
        wallet keypair, which must be the job's `judge` (the contract enforces
        this). On a pass, call `release_bounty` next. `evidence_root` must match
        the anchored submission.
        """
        return self.context.call_contract(
            contract_id=self.board_address,
            function_name="record_verdict",
            args=[self.context.keypair.public_key, u64(job_id), bool(passed),
                  u32(int(round(score))), evidence_root],
        )

    def release_bounty(self, job_id: int, evidence_root: bytes):
        """
        Disburse the escrow for a verified job — full bounty to the single
        claimant, or an N-way split across swarm members per their recorded
        shares. Must be signed by the job's `judge` (the escrow's release
        authority). Call after a passing `record_verdict`.
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
            return router.split_release(escrow_id, pairs, evidence_root)
        # Single payout: pay the claimant the whole bounty (1-way split keeps one
        # release path and lets the escrow re-check the balance).
        return router.split_release(escrow_id, [(job["agent"], 10000)], evidence_root)

    def settle(self, job_id: int, passed: bool, score: int, evidence_root: bytes):
        """
        Judge-side convenience: record the verdict (+score) and, on a pass,
        release the bounty — both signed by the judge keypair. Returns the release
        TxResult on a pass, or the verdict TxResult on a fail.
        """
        verdict_tx = self.record_verdict(job_id, passed, score, evidence_root)
        if not passed:
            return verdict_tx
        return self.release_bounty(job_id, evidence_root)

    # ── orchestration ─────────────────────────────────────────────────────────
    def execute_job(self, job_id: int, model_spec: Optional[str] = None, *,
                    complete_fn=None, claim: bool = False, revise: bool = True,
                    evidence_uri: str = "", api_key: Optional[str] = None):
        """
        Agent side, any job type: read the job's `Rubric` from chain, do the work
        with a chosen `model_spec` (``provider:model``), and submit the real
        evidence on-chain. Returns `(EvidenceBundle, content_text)`. Signed by this
        client's keypair (the working agent). Set `claim=True` to self-claim an
        open single-mode job first.
        """
        from mycelium_sdk.proof.worker import ContentAgent

        rubric = self.fetch_rubric(job_id)
        if claim and self.get_job(job_id).get("status") == "open":
            self.claim_job(job_id)

        if complete_fn is not None:
            agent = ContentAgent(self.context.keypair, complete_fn, model=model_spec or "agent")
        else:
            if not model_spec:
                raise ValueError("execute_job needs a model_spec (provider:model) or a complete_fn.")
            agent = ContentAgent.from_model(self.context.keypair, model_spec, api_key=api_key)

        bundle, content = agent.do_job(job_id, rubric, revise=revise)
        self.submit_evidence(job_id, bundle.evidence_root(), evidence_uri)
        return bundle, content

    def judge_and_settle(self, job_id: int, evidence_bundle, content_views=None,
                         *, oracle=None, api_keys=None, reputation_address=None):
        """
        Run the verification the job specifies and settle accordingly — the
        end-to-end judge step. Reads the job's on-chain `spec` to rebuild the
        exact `Rubric` (checks + the poster's chosen judge panel), runs that
        heterogeneous panel over the real evidence, records the panel's verdict
        (+score) on-chain, and on a pass releases the bounty (single payout or
        swarm split). Signed by this client's keypair, which must be the job's
        judge. Returns the `PanelResult`.

        `content_views` maps an artifact uri → the extracted text the panel reads
        (the actual deliverable). `api_keys` maps provider → key (else env). If
        `reputation_address` is given, the worker(s) are credited the verdict score
        there (this client must be that registry's recorder).
        """
        from mycelium_sdk.proof.panel import JudgePanel

        rubric = self.fetch_rubric(job_id)
        panel = JudgePanel.from_rubric(self.context.keypair, rubric, oracle=oracle,
                                       api_keys=api_keys or {})
        result = panel.evaluate(rubric, evidence_bundle, artifact_views=content_views or {})

        root = evidence_bundle.evidence_root()
        self.record_verdict(job_id, result.passed, result.weighted_score, root)

        # Build and save detailed Critique JSON report (Standardized Judge Verdict Explanations)
        try:
            import os
            import json

            # Build markdown critique
            md = []
            md.append(f"# Judge Panel Critique Report — Job #{job_id}")
            md.append(f"**Rubric Title**: {rubric.job}")
            status_str = "PASS ✅" if result.passed else "FAIL ❌"
            md.append(f"**Final Verdict**: {status_str} (Weighted Score: {result.weighted_score:.1f} / 100)")
            models_list = result.verdict.model.replace("panel:", "").split(",")
            md.append(f"**Models on Panel**: {', '.join(models_list)}\n")
            md.append("## Criteria Breakdown")
            for cs in result.verdict.scores:
                md.append(f"### Criterion: `{cs.id}` (Score: {cs.score}/100)")
                md.append(f"- **Rationale**: {cs.rationale}")
                spread = result.disagreement.get(cs.id, 0.0)
                md.append(f"- **Disagreement Spread**: {spread:.1f}")
                md.append("\n**Seat Details**:")
                for sv in result.seat_verdicts:
                    for s_score in sv.scores:
                        if s_score.id == cs.id:
                            md.append(f"  - *{sv.model}*: Score **{s_score.score}** — \"{s_score.rationale}\"")
                md.append("")
            critique_md = "\n".join(md)

            critique_data = {
                "job_id": job_id,
                "rubric_hash": rubric.rubric_hash().hex(),
                "passed": bool(result.passed),
                "weighted_score": float(result.weighted_score),
                "critique_markdown": critique_md,
                "criteria_scores": {
                    cs.id: {
                        "score": cs.score,
                        "disagreement": float(result.disagreement.get(cs.id, 0.0)),
                        "details": [
                            {
                                "model": sv.model,
                                "score": next((s.score for s in sv.scores if s.id == cs.id), 0),
                                "rationale": next((s.rationale for s in sv.scores if s.id == cs.id), "")
                            }
                            for sv in result.seat_verdicts
                        ]
                    }
                    for cs in result.verdict.scores
                }
            }

            os.makedirs(os.path.join(".mycelium", "critiques"), exist_ok=True)
            critique_path = os.path.join(".mycelium", "critiques", f"job_{job_id}_critique.json")
            with open(critique_path, "w", encoding="utf-8") as f:
                json.dump(critique_data, f, indent=2)
            _log.info("Saved judge panel critique to %s", critique_path)
        except Exception as err:
            _log.error("Failed to generate or save critique: %s", err)

        if result.passed:
            self.release_bounty(job_id, root)
        if reputation_address:
            self._credit_reputation(job_id, reputation_address, int(round(result.weighted_score)), result.passed)
        return result

    def _credit_reputation(self, job_id: int, reputation_address: str, score: int, passed: bool):
        """Credit the verdict score to the worker(s) — single agent, or each swarm
        member (they collaborated on the verified work). Best-effort; signed by the
        judge (which must be the registry's recorder)."""
        from mycelium_sdk.proof.reputation import ReputationClient

        rep = ReputationClient(self.context, reputation_address)
        job = self.get_job(job_id)
        if job.get("mode") == "swarm":
            workers = [_addr_str(m) for m in self.get_swarm(job_id)]
        else:
            workers = [job.get("agent")]
        for w in workers:
            if w:
                rep.credit(w, job_id, score, passed)

    def fetch_rubric(self, job_id: int):
        """Rebuild the job's `Rubric` from its on-chain `spec` — the canonical
        source of truth (checks + judge panel), no off-chain dependency."""
        import json as _json
        from mycelium_sdk.proof.rubric import Rubric

        job = self.get_job(job_id)
        spec = job.get("spec")
        if not spec:
            raise RuntimeError(f"Job {job_id} has no on-chain spec to judge against.")
        return Rubric.from_dict(_json.loads(spec))

    def finalize(self, job_id: int):
        """
        Poster closes the record of a verified job (marks it `done`). The bounty
        is already released by the judge at verdict time, so this is bookkeeping;
        the contract `finalize` requires the poster's auth.
        """
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
            except Exception as exc:
                # Read-only discovery: skip an unreadable job rather than abort
                # the whole scan, but log at debug so the failure isn't invisible.
                _log.debug("list_open_jobs: skipping job %s (%s)", job_id, exc)
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
        score = get("score")

        def _txt(v):
            v = _bytes_to_str(v)
            return v if isinstance(v, str) else (v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else v)

        def _hex(v):
            return v.hex() if isinstance(v, (bytes, bytearray)) else (v or "")

        return {
            "job_id": job_id,
            "poster": _addr_str(get("poster")),
            "title": _txt(get("title")) or "",
            "description": _txt(get("description")) or "",
            "spec": _txt(get("spec")) or "",                 # full rubric JSON (checks + judges)
            "rubric_hash": _hex(get("rubric_hash")),
            "evidence_root": _hex(get("evidence_root")),
            "evidence_uri": _txt(get("evidence_uri")) or "",
            "score": int(score) if score is not None else 0,
            "bounty_stroops": int(bounty) if bounty is not None else 0,
            "token": _addr_str(get("token")),
            "mode": str(get("mode")) if get("mode") is not None else None,
            "escrow": _addr_str(get("escrow")),
            "judge": _addr_str(get("judge")),
            "deadline": int(deadline) if deadline is not None else 0,
            "status": str(get("status")) if get("status") is not None else None,
            "agent": _addr_str(get("agent")),
        }
