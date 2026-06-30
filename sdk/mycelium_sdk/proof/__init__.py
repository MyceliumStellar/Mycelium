"""
Mycelium proof layer — verifiable agent work.

A hash proves *integrity* (specific bytes existed); it cannot prove *validity*
(a deliverable satisfies a request). "I need a Canva pitch deck" has no preimage.
This package replaces the old `SHA256(proof) == spec_hash` tautology with a layer
that asks the real question — *did the work meet the agreed acceptance criteria?*

The pieces (see ``PROOF_SYSTEM.md`` for the full architecture):

- ``Rubric`` / ``Criterion`` — the poster's acceptance criteria, committed at post
  time. ``rubric_hash`` is anchored on-chain; each criterion routes to the cheapest
  verifier (``deterministic`` → code, ``llm`` → judge panel).
- ``EvidenceBundle`` — what the worker actually submits: the deliverable artifacts,
  per-criterion claims, and provenance. ``evidence_root`` is anchored on-chain; the
  bundle lives off-chain (reusing the memory anchor pattern).
- ``Judge`` — an LLM judge that scores ``llm`` criteria against the rubric + bundle
  and runs ``deterministic`` criteria through a pluggable oracle, then signs a
  ``Verdict`` with its Stellar key.
- ``Verdict`` — the per-criterion scores, the weighted pass/fail, and the judge's
  signature. P0 trusts a single judge; P1+ aggregate a heterogeneous panel by
  median (see the phased plan in ``PROOF_SYSTEM.md``).
"""

from mycelium_sdk.proof.rubric import Criterion, Rubric, CriterionType
from mycelium_sdk.proof.evidence import Artifact, Claim, EvidenceBundle
from mycelium_sdk.proof.verdict import CriterionScore, Verdict
from mycelium_sdk.proof.judge import Judge, OracleResult
from mycelium_sdk.proof.panel import JudgePanel, Seat, PanelResult
from mycelium_sdk.proof.worker import ContentAgent
from mycelium_sdk.proof.registry import VerifierRegistryClient
from mycelium_sdk.proof.reputation import ReputationClient
from mycelium_sdk.proof.providers import (
    openai_chat_completer, resolve_completer, list_models, split_spec,
    nvidia, PROVIDERS, NVIDIA_BASE_URL, GROQ_BASE_URL,
)

__all__ = [
    "Criterion",
    "CriterionType",
    "Rubric",
    "Artifact",
    "Claim",
    "EvidenceBundle",
    "CriterionScore",
    "Verdict",
    "Judge",
    "OracleResult",
    "JudgePanel",
    "Seat",
    "PanelResult",
    "ContentAgent",
    "VerifierRegistryClient",
    "ReputationClient",
    "openai_chat_completer",
    "resolve_completer",
    "list_models",
    "split_spec",
    "nvidia",
    "PROVIDERS",
    "NVIDIA_BASE_URL",
    "GROQ_BASE_URL",
]
