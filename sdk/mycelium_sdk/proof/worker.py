"""
ContentAgent — an actual AI agent that does the work, not a script.

Given a posted job's `Rubric`, the agent uses an LLM to *produce the deliverable*
(here, written content), then critiques its own draft against each acceptance
criterion and revises — a real draft → self-review → revise loop, not a single
canned call. The output is packaged as an `EvidenceBundle` carrying the actual
content (and per-criterion claims), signed with the agent's Stellar key.

This is deliberately model-agnostic (a `complete_fn` from `proof.providers`), so
the worker runs on a *different* family than any judge seat — the worker should
never grade its own homework.

The proof the agent submits is the real content, content-addressed; only the
32-byte `evidence_root` goes on-chain (`submit_evidence`). The judges read the
actual text off the bundle, so a hash is never mistaken for proof of quality.
"""

from typing import Callable, Dict, List, Optional

from mycelium_sdk.proof.rubric import Rubric
from mycelium_sdk.proof.evidence import Artifact, Claim, EvidenceBundle
from mycelium_sdk.logging import get_logger

_log = get_logger("proof.worker")


class ContentAgent:
    def __init__(self, keypair, complete_fn: Callable[[str, str], str], *, model: str = "worker"):
        """`complete_fn(system, user) -> text` is the agent's brain (e.g. a
        Llama/Mistral model via `proof.providers`). `keypair` signs the bundle."""
        self.keypair = keypair
        self.complete_fn = complete_fn
        self.model = model

    @classmethod
    def from_model(cls, keypair, spec: str, *, api_key: Optional[str] = None, **completer_kw) -> "ContentAgent":
        """Build an agent whose brain is a chosen ``provider:model`` (e.g.
        ``groq:llama-3.3-70b-versatile``). One line to pick which model does the work."""
        from mycelium_sdk.proof.providers import resolve_completer

        return cls(keypair, resolve_completer(spec, api_key=api_key, **completer_kw), model=spec)

    # ── the work ────────────────────────────────────────────────────────────────
    def do_job(self, job_id: int, rubric: Rubric, *, revise: bool = True) -> tuple:
        """
        Produce the deliverable for `rubric` and return (EvidenceBundle, text).

        Two reasoning passes by default: a draft, then a self-review against the
        rubric and a revision. Set `revise=False` for a single pass.
        """
        _log.info("worker %s starting job #%s: %s", self.model, job_id, rubric.job)
        draft = self._draft(rubric)
        content = self._revise(rubric, draft) if revise else draft

        bundle = EvidenceBundle(
            job_id=job_id,
            rubric_hash=rubric.rubric_hash().hex(),
            artifacts=[Artifact.from_bytes(
                "deliverable", "inline://deliverable.md", content.encode("utf-8"), "text/markdown",
            )],
            claims=[Claim(c.id, note=f"addressed: {c.check}") for c in rubric.criteria],
            provenance={"produced_by": self.keypair.public_key, "model": self.model, "passes": 2 if revise else 1},
        ).sign(self.keypair)
        return bundle, content

    # ── passes ──────────────────────────────────────────────────────────────────
    def _draft(self, rubric: Rubric) -> str:
        criteria = "\n".join(f"- {c.id} (weight {c.weight}): {c.check}" for c in rubric.criteria)
        system = (
            "You are a professional content-creation agent fulfilling a paid, on-chain bounty. "
            "Produce the deliverable itself — no preamble, no meta commentary, just the finished work. "
            "It will be judged by an independent panel against the acceptance criteria, so satisfy every one."
        )
        user = (
            f"JOB: {rubric.job}\n"
            f"DELIVERABLE TYPE: {rubric.deliverable_type}\n\n"
            f"ACCEPTANCE CRITERIA (you must satisfy each):\n{criteria}\n\n"
            f"Pass threshold: {rubric.pass_threshold}/100. Write the deliverable now."
        )
        return self.complete_fn(system, user).strip()

    def _revise(self, rubric: Rubric, draft: str) -> str:
        criteria = "\n".join(f"- {c.id} (weight {c.weight}): {c.check}" for c in rubric.criteria)
        system = (
            "You are the same content agent, now self-reviewing before submission. "
            "Critique the draft against EACH criterion honestly, then output ONLY the improved final "
            "deliverable (no critique, no preamble)."
        )
        user = (
            f"JOB: {rubric.job}\n\nCRITERIA:\n{criteria}\n\n"
            f"DRAFT:\n<<<\n{draft}\n>>>\n\n"
            "Fix any criterion the draft underserves, then output the final deliverable only."
        )
        revised = self.complete_fn(system, user).strip()
        return revised or draft
