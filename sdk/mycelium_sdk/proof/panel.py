"""
JudgePanel — a heterogeneous multi-LLM jury over one submission.

Each seat is a different model family (e.g. MiniMax, DeepSeek, Mistral) scoring
the SAME evidence against the SAME rubric, independently. The panel takes the
**per-criterion median** across seats, weights it by the rubric, and passes iff
the weighted median clears the threshold. Median (not mean) means a single rogue
or fooled seat can't swing the verdict — you'd have to corrupt a majority of
independent families at once, which is the whole point of diversity.

This is the P1 realisation of "multiple LLMs as a judge": the on-chain
`record_verdict` is driven by this aggregate, not by any single model and not by
a hardcoded decision. Each seat's raw scores are returned too, so the verdict is
explainable — you can see where the models agreed and disagreed.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from mycelium_sdk.proof.rubric import Rubric
from mycelium_sdk.proof.evidence import EvidenceBundle
from mycelium_sdk.proof.verdict import CriterionScore, Verdict
from mycelium_sdk.proof.judge import Judge, OracleFn
from mycelium_sdk.logging import get_logger

_log = get_logger("proof.panel")


def _median(xs: List[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


@dataclass
class Seat:
    """One panelist: a label (the model id) and its completion backend."""
    model: str
    complete_fn: Callable[[str, str], str]


@dataclass
class PanelResult:
    verdict: Verdict                       # the aggregate (median), signed by the panel key
    passed: bool
    weighted_score: float
    seat_verdicts: List[Verdict]           # each model's own scoring, for transparency
    disagreement: Dict[str, float]         # criterion id -> score spread (max-min) across seats


class JudgePanel:
    def __init__(self, keypair, seats: List[Seat], *, oracle: Optional[OracleFn] = None):
        if not seats:
            raise ValueError("A panel needs at least one seat.")
        self.keypair = keypair
        self.seats = seats
        self.oracle = oracle

    @classmethod
    def from_rubric(cls, keypair, rubric, *, oracle: Optional[OracleFn] = None,
                    api_keys: Optional[Dict[str, str]] = None, **completer_kw) -> "JudgePanel":
        """
        Build the panel the *job itself* prescribes: one seat per
        ``rubric.judge_models`` entry (``provider:model``), resolved across
        providers (NVIDIA, Groq, …). This is what makes verification reproducible
        and agreed — the poster committed to these judges and their hash is anchored
        on-chain, so nobody can quietly swap the jury.
        """
        from mycelium_sdk.proof.providers import resolve_completer, split_spec

        specs = rubric.judge_specs()
        if not specs:
            raise ValueError(
                "Rubric names no judge models; set judge_models when posting the job."
            )
        api_keys = api_keys or {}
        seats = []
        for spec in specs:
            provider, _ = split_spec(spec)
            seats.append(Seat(spec, resolve_completer(spec, api_key=api_keys.get(provider), **completer_kw)))
        return cls(keypair, seats, oracle=oracle)

    def evaluate(
        self,
        rubric: Rubric,
        bundle: EvidenceBundle,
        *,
        artifact_views: Optional[Dict[str, str]] = None,
    ) -> PanelResult:
        weights = {c.id: c.weight for c in rubric.criteria}

        # Each seat scores independently. A seat that errors is dropped (with a
        # log) rather than tanking the whole panel — but we never silently pass.
        seat_verdicts: List[Verdict] = []
        for seat in self.seats:
            judge = Judge(self.keypair, model=seat.model, oracle=self.oracle,
                          complete_fn=seat.complete_fn)
            try:
                seat_verdicts.append(judge.evaluate(rubric, bundle, artifact_views=artifact_views))
                _log.info("panel seat %s scored the submission", seat.model)
            except Exception as exc:  # noqa: BLE001
                _log.error("panel seat %s failed (%s); dropping it", seat.model, exc)

        if not seat_verdicts:
            raise RuntimeError("Every panel seat failed; no verdict can be formed.")

        # Per-criterion median across seats.
        per_criterion: Dict[str, List[float]] = {}
        for v in seat_verdicts:
            for s in v.scores:
                per_criterion.setdefault(s.id, []).append(s.score)

        agg_scores: List[CriterionScore] = []
        disagreement: Dict[str, float] = {}
        for c in rubric.criteria:
            xs = per_criterion.get(c.id, [])
            med = _median(xs) if xs else 0.0
            disagreement[c.id] = (max(xs) - min(xs)) if xs else 0.0
            agg_scores.append(CriterionScore(
                c.id, int(round(med)),
                rationale=f"panel median of {len(xs)} seats (spread {disagreement[c.id]:.0f})",
            ))

        verdict = Verdict(
            job_id=bundle.job_id,
            rubric_hash=rubric.rubric_hash().hex(),
            evidence_root=bundle.evidence_root().hex(),
            scores=agg_scores,
            pass_threshold=rubric.pass_threshold,
            model="panel:" + ",".join(s.model for s in self.seats),
        ).sign(self.keypair)

        weighted = verdict.weighted_total(weights)
        return PanelResult(
            verdict=verdict,
            passed=weighted >= rubric.pass_threshold,
            weighted_score=weighted,
            seat_verdicts=seat_verdicts,
            disagreement=disagreement,
        )
