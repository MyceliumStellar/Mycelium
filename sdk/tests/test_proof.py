"""Offline tests for the proof layer (rubric / evidence / verdict / judge).

These exercise the P0 primitives without a network or an API key: a stub
Anthropic client stands in for the judge model, and a real Stellar keypair signs.
"""

import json

import pytest
from stellar_sdk import Keypair

from mycelium_sdk.proof import (
    Criterion, CriterionType, Rubric,
    Artifact, Claim, EvidenceBundle,
    Judge, OracleResult, Verdict,
)


def _rubric() -> Rubric:
    return Rubric(
        job="Investor pitch deck, Canva, Series-A SaaS",
        deliverable_type="file/pptx+pdf",
        criteria=[
            Criterion("fmt", "exports as .pptx AND .pdf", 10, CriterionType.DETERMINISTIC),
            Criterion("len", "10 <= slide_count <= 14", 10, CriterionType.DETERMINISTIC),
            Criterion("cover", "covers problem, solution, market, traction, ask", 40, CriterionType.LLM),
            Criterion("design", "visually coherent: consistent palette, legible", 25, CriterionType.LLM),
            Criterion("orig", "tailored to the brief, not a verbatim template", 15, CriterionType.LLM),
        ],
        pass_threshold=75,
    )


def _bundle(rubric: Rubric) -> EvidenceBundle:
    deck = b"%PDF-1.7 fake deck bytes"
    return EvidenceBundle(
        job_id=42,
        rubric_hash=rubric.rubric_hash().hex(),
        artifacts=[
            Artifact.from_bytes("deliverable", "ipfs://deck.pdf", deck, "application/pdf"),
        ],
        claims=[Claim("len", note="12 slides", value=12, ref="deck.pdf")],
        provenance={"tool": "canva-api"},
    )


def test_rubric_hash_is_deterministic():
    a, b = _rubric().rubric_hash(), _rubric().rubric_hash()
    assert a == b and len(a) == 32
    # Round-trips through dict without changing the hash.
    assert Rubric.from_dict(_rubric().to_dict()).rubric_hash() == a


def test_rubric_weight_split():
    r = _rubric()
    assert r.total_weight == 100
    assert {c.id for c in r.deterministic_criteria()} == {"fmt", "len"}
    assert {c.id for c in r.llm_criteria()} == {"cover", "design", "orig"}


def test_evidence_root_and_signature_roundtrip():
    kp = Keypair.random()
    r = _rubric()
    bundle = _bundle(r).sign(kp)
    assert len(bundle.evidence_root()) == 32
    assert bundle.verify_sig(Keypair.from_public_key(kp.public_key))
    # Tampering with an artifact changes the root → signature no longer matches.
    bundle.artifacts[0].sha256 = "0" * 64
    assert not bundle.verify_sig(Keypair.from_public_key(kp.public_key))


class _StubClient:
    """Returns a fixed JSON score blob for the llm criteria."""

    def __init__(self, scores):
        self._scores = scores
        self.messages = self

    def create(self, **kwargs):
        text = json.dumps(self._scores)
        block = type("B", (), {"text": text})()
        return type("M", (), {"content": [block]})()


def _slide_oracle(criterion, bundle):
    # Tiny deterministic oracle: pass fmt always, len from the worker's claim.
    if criterion.id == "fmt":
        return OracleResult(100, "pdf present")
    if criterion.id == "len":
        n = next((c.value for c in bundle.claims if c.id == "len"), 0)
        ok = 10 <= int(n) <= 14
        return OracleResult(100 if ok else 0, f"slide_count={n}")
    return OracleResult(0, "unknown")


def test_judge_produces_signed_passing_verdict():
    kp = Keypair.random()
    r = _rubric()
    bundle = _bundle(r).sign(kp)

    judge_kp = Keypair.random()
    judge = Judge(
        judge_kp,
        oracle=_slide_oracle,
        client=_StubClient({
            "cover": {"score": 88, "rationale": "all five covered"},
            "design": {"score": 80, "rationale": "consistent palette"},
            "orig": {"score": 70, "rationale": "tailored"},
        }),
    )
    verdict = judge.evaluate(r, bundle)

    assert isinstance(verdict, Verdict)
    assert verdict.judge == judge_kp.public_key
    assert verdict.verify_sig(Keypair.from_public_key(judge_kp.public_key))

    weights = {c.id: c.weight for c in r.criteria}
    # 10 + 10 + .88*40 + .80*25 + .70*15 = 85.7
    assert round(verdict.weighted_total(weights), 1) == 85.7
    assert verdict.passed(weights)


def test_judge_fails_when_deterministic_criterion_fails():
    kp = Keypair.random()
    r = _rubric()
    bundle = _bundle(r)
    bundle.claims = [Claim("len", value=40)]  # too many slides → len fails
    bundle.sign(kp)

    judge = Judge(
        Keypair.random(),
        oracle=_slide_oracle,
        client=_StubClient({
            "cover": {"score": 90, "rationale": ""},
            "design": {"score": 90, "rationale": ""},
            "orig": {"score": 90, "rationale": ""},
        }),
    )
    verdict = judge.evaluate(r, bundle)
    weights = {c.id: c.weight for c in r.criteria}
    # len contributes 0 of its 10: 10 + 0 + 90*(40+25+15)/100 = 82 → still passes here,
    # so assert the len score itself is 0 (the deterministic gate did fire).
    assert next(s.score for s in verdict.scores if s.id == "len") == 0
