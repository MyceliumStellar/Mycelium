"""
Verdict — a judge's signed evaluation of an evidence bundle against a rubric.

A verdict is the unit the verification market aggregates. It carries a score per
criterion (0..100), the weighted total, the pass/fail against the rubric
threshold, and the judge's signature over a commitment to all of it. In P0 a
single trusted judge's verdict gates escrow release; in P1+ the contract takes
the per-criterion *median* across a heterogeneous panel (see ``PROOF_SYSTEM.md``).

The signature is over ``verdict_root`` — a hash binding the verdict to the exact
``rubric_hash`` and ``evidence_root`` it judged, so a verdict can never be
replayed against a different job or submission.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class CriterionScore:
    id: str
    score: int          # 0..100
    rationale: str = ""

    def __post_init__(self):
        self.score = max(0, min(100, int(self.score)))

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "score": self.score, "rationale": self.rationale}


@dataclass
class Verdict:
    job_id: int
    rubric_hash: str            # hex — the rubric this verdict judged
    evidence_root: str          # hex — the submission this verdict judged
    scores: List[CriterionScore]
    pass_threshold: int
    judge: str = ""             # judge's Stellar public key
    model: str = ""             # which model produced it (panel diversity audit)
    created_at: Optional[int] = None
    sig: Optional[str] = None   # hex ed25519 sig over verdict_root

    # ── aggregation ──────────────────────────────────────────────────────────
    def weighted_total(self, weights: Dict[str, int]) -> float:
        """Weighted score on a 0..100 scale. ``weights`` maps criterion id →
        weight (from the rubric); the total weight need not be 100."""
        total_w = sum(weights.get(s.id, 0) for s in self.scores)
        if total_w <= 0:
            return 0.0
        return sum(s.score * weights.get(s.id, 0) for s in self.scores) / total_w

    def passed(self, weights: Dict[str, int]) -> bool:
        return self.weighted_total(weights) >= self.pass_threshold

    # ── serialization / signing ────────────────────────────────────────────────
    def to_dict(self, *, include_sig: bool = True) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "job_id": self.job_id,
            "rubric_hash": self.rubric_hash,
            "evidence_root": self.evidence_root,
            "scores": [s.to_dict() for s in self.scores],
            "pass_threshold": self.pass_threshold,
            "judge": self.judge,
            "model": self.model,
            "created_at": self.created_at,
        }
        if include_sig and self.sig is not None:
            d["sig"] = self.sig
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Verdict":
        return cls(
            job_id=int(d["job_id"]),
            rubric_hash=d["rubric_hash"],
            evidence_root=d["evidence_root"],
            scores=[CriterionScore(id=s["id"], score=s["score"], rationale=s.get("rationale", ""))
                    for s in d["scores"]],
            pass_threshold=int(d["pass_threshold"]),
            judge=d.get("judge", ""),
            model=d.get("model", ""),
            created_at=d.get("created_at"),
            sig=d.get("sig"),
        )

    def canonical_json(self, *, include_sig: bool = False) -> bytes:
        return json.dumps(
            self.to_dict(include_sig=include_sig),
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")

    def verdict_root(self) -> bytes:
        """32-byte commitment the judge signs (and, in P2, what a commit-reveal
        ``commit = SHA256(verdict_root ‖ salt)`` is built from)."""
        return hashlib.sha256(self.canonical_json(include_sig=False)).digest()

    def sign(self, keypair) -> "Verdict":
        if self.created_at is None:
            self.created_at = int(time.time())
        self.judge = keypair.public_key
        self.sig = keypair.sign(self.verdict_root()).hex()
        return self

    def verify_sig(self, keypair) -> bool:
        """Verify the verdict was signed by ``keypair`` (built from ``self.judge``)."""
        if not self.sig:
            return False
        try:
            keypair.verify(self.verdict_root(), bytes.fromhex(self.sig))
            return True
        except Exception:
            return False
