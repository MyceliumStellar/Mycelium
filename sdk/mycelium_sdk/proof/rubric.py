"""
Rubric — the poster's structured acceptance criteria.

The root cause of the old hash tautology was that ``post_job`` took an opaque
``spec_hash``: "I need a Canva pitch deck" was never decomposed into anything
checkable. A ``Rubric`` is that decomposition — a weighted list of criteria, each
tagged with the *kind* of verification it needs:

- ``deterministic`` — settled by code (file format, slide count, tests). Free.
- ``llm``           — settled by the judge panel (coverage, design, originality).

The poster commits to the rubric at post time: ``rubric_hash`` (SHA-256 of the
canonical JSON) is anchored on-chain, the full rubric lives off-chain. A
submission is bound to the exact rubric it was judged against by that hash.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List


class CriterionType:
    DETERMINISTIC = "deterministic"
    LLM = "llm"

    ALL = (DETERMINISTIC, LLM)


@dataclass
class Criterion:
    """
    One acceptance criterion. ``check`` is the natural-language requirement (the
    instruction handed to the oracle or the judge). ``weight`` is its share of the
    total score; a job passes when the weighted score clears ``Rubric.pass_threshold``.
    """

    id: str
    check: str
    weight: int
    type: str = CriterionType.LLM

    def __post_init__(self):
        if self.type not in CriterionType.ALL:
            raise ValueError(
                f"Criterion {self.id!r}: type must be one of {CriterionType.ALL} "
                f"(got {self.type!r})."
            )
        if self.weight <= 0:
            raise ValueError(f"Criterion {self.id!r}: weight must be positive (got {self.weight}).")
        if not self.id:
            raise ValueError("Criterion id must be non-empty.")

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "type": self.type, "check": self.check, "weight": self.weight}


@dataclass
class Rubric:
    """
    A poster's full acceptance contract for a job. ``pass_threshold`` is on the
    same 0..100 scale the weighted criterion scores aggregate to.
    """

    job: str                                              # the job DESCRIPTION (required)
    criteria: List[Criterion]                             # the checks (required, >=1)
    pass_threshold: int = 75
    deliverable_type: str = "any"
    title: str = ""                                       # the job HEADING (shown on the bounty page)
    judge_models: List[str] = field(default_factory=list) # poster-chosen panel: ["provider:model", ...]
    aggregate: str = "median"                             # how seat scores combine
    version: int = 2
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.criteria:
            raise ValueError("A rubric needs at least one check.")
        if not self.job:
            raise ValueError("A rubric needs a description (job).")
        ids = [c.id for c in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Check ids must be unique (got {ids}).")
        if not 0 < self.pass_threshold <= 100:
            raise ValueError(f"pass_threshold must be in (0, 100] (got {self.pass_threshold}).")
        if not self.title:
            # Fall back to a heading derived from the description's first line.
            self.title = self.job.strip().splitlines()[0][:80]

    @property
    def description(self) -> str:
        return self.job

    def judge_specs(self) -> List[str]:
        """The poster's chosen judge model specs (``provider:model``)."""
        return list(self.judge_models)

    @property
    def total_weight(self) -> int:
        return sum(c.weight for c in self.criteria)

    def llm_criteria(self) -> List[Criterion]:
        return [c for c in self.criteria if c.type == CriterionType.LLM]

    def deterministic_criteria(self) -> List[Criterion]:
        return [c for c in self.criteria if c.type == CriterionType.DETERMINISTIC]

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "version": self.version,
            "title": self.title,
            "job": self.job,
            "deliverable_type": self.deliverable_type,
            "criteria": [c.to_dict() for c in self.criteria],
            "pass_threshold": self.pass_threshold,
            # The chosen panel is part of the committed rubric — anchoring its hash
            # means a poster cannot swap judges after the fact.
            "judges": {"models": list(self.judge_models), "aggregate": self.aggregate},
        }
        if self.extra:
            d["extra"] = self.extra
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Rubric":
        judges = d.get("judges", {}) or {}
        return cls(
            job=d["job"],
            title=d.get("title", ""),
            criteria=[
                Criterion(
                    id=c["id"],
                    check=c["check"],
                    weight=int(c["weight"]),
                    type=c.get("type", CriterionType.LLM),
                )
                for c in d["criteria"]
            ],
            pass_threshold=int(d.get("pass_threshold", 75)),
            deliverable_type=d.get("deliverable_type", "any"),
            judge_models=list(judges.get("models", [])),
            aggregate=judges.get("aggregate", "median"),
            version=int(d.get("version", 2)),
            extra=d.get("extra", {}) or {},
        )

    def canonical_json(self) -> bytes:
        """
        Deterministic serialization for hashing. Sorted keys + compact separators
        so the same rubric always produces the same ``rubric_hash`` on any machine.
        """
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def rubric_hash(self) -> bytes:
        """The 32-byte commitment anchored on-chain at post time."""
        return hashlib.sha256(self.canonical_json()).digest()
