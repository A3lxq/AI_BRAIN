"""The retrieval evaluation harness (docs/design/retrieval-pipeline.md §2.6;
docs/TESTING_STRATEGY.md's retrieval-evaluation-corpus specification).

Computes Recall@K/Precision@K (K=3,5,10), MRR, nDCG@10, and latency
percentiles against a hand-curated corpus of questions with relevance
judgments. The metric computation here is fully testable without a live
retrieval stack (pure math over synthetic rankings); running it against the
starter corpus needs a real vault + Qdrant server, currently blocked in this
development environment (design doc §8, unchanged since Phase 3).
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

__all__ = [
    "RelevanceJudgment",
    "Question",
    "EvaluationReport",
    "SearchFn",
    "run_evaluation",
    "load_corpus",
]

#: The starter 10-note/17-question corpus design doc §2.6 explicitly ships
#: instead of TESTING_STRATEGY.md's full 30-60 note target. Resolved
#: relative to the repo checkout (`src/../tests/...`) -- this only works
#: when ATHENA AI-BRAIN is run from its own repo, not as an installed wheel
#: elsewhere; acceptable for this project's current pre-distribution state,
#: but flagged here so it isn't assumed correct if that ever changes.
DEFAULT_CORPUS_DIR = (
    Path(__file__).resolve().parents[3] / "tests" / "retrieval" / "fixtures" / "eval_corpus"
)

SearchFn = Callable[[str], Awaitable[list[str]]]

_K_VALUES = (3, 5, 10)


@dataclass(frozen=True)
class RelevanceJudgment:
    note_path: str
    grade: int  # 0 = not relevant, 1 = relevant, 2 = highly relevant


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    judgments: list[RelevanceJudgment]  # empty list = deliberately unanswerable


@dataclass(frozen=True)
class EvaluationReport:
    recall_at_k: dict[int, float]
    precision_at_k: dict[int, float]
    mrr: float
    ndcg_at_10: float
    p50_latency_ms: float
    p95_latency_ms: float
    num_questions: int
    num_answerable: int
    unanswerable_top1_false_positive_rate: float


def load_corpus(corpus_dir: Path) -> list[Question]:
    """Reads `questions.json` (a flat list of {id, text, judgments: [{note_path,
    grade}]} objects) from `corpus_dir` -- versioned as plain JSON fixtures in
    Git per TESTING_STRATEGY.md's "not generated at runtime" requirement."""
    data = json.loads((corpus_dir / "questions.json").read_text(encoding="utf-8"))
    return [
        Question(
            id=item["id"],
            text=item["text"],
            judgments=[
                RelevanceJudgment(note_path=j["note_path"], grade=j["grade"])
                for j in item["judgments"]
            ],
        )
        for item in data
    ]


def _dcg(grades: list[int]) -> float:
    return sum(grade / math.log2(rank + 1) for rank, grade in enumerate(grades, start=1))


async def run_evaluation(corpus: list[Question], search_fn: SearchFn) -> EvaluationReport:
    """`search_fn` returns a ranked list of vault-relative note paths for one
    query -- `athena.retrieval.search.search_ranked_note_paths` is the real
    implementation; tests pass a synthetic stub instead, per design doc §7's
    explicit split between "the harness computes correct numbers" (this
    function, unit-testable) and "the pipeline retrieves well" (integration,
    needs a live stack).
    """
    recall_hits: dict[int, list[float]] = {k: [] for k in _K_VALUES}
    precision_hits: dict[int, list[float]] = {k: [] for k in _K_VALUES}
    reciprocal_ranks: list[float] = []
    ndcg_scores: list[float] = []
    latencies_ms: list[float] = []
    unanswerable_total = 0
    unanswerable_false_positives = 0

    for question in corpus:
        start = perf_counter()
        ranked_paths = await search_fn(question.text)
        latencies_ms.append((perf_counter() - start) * 1000)

        relevant_paths = {j.note_path for j in question.judgments if j.grade > 0}

        if not relevant_paths:
            unanswerable_total += 1
            if ranked_paths:
                unanswerable_false_positives += 1
            continue

        for k in _K_VALUES:
            top_k = ranked_paths[:k]
            hit_count = len(set(top_k) & relevant_paths)
            recall_hits[k].append(hit_count / len(relevant_paths))
            precision_hits[k].append(hit_count / k)

        rr = 0.0
        for rank, path in enumerate(ranked_paths, start=1):
            if path in relevant_paths:
                rr = 1.0 / rank
                break
        reciprocal_ranks.append(rr)

        grade_by_path = {j.note_path: j.grade for j in question.judgments}
        actual_grades = [grade_by_path.get(path, 0) for path in ranked_paths[:10]]
        ideal_grades = sorted((j.grade for j in question.judgments), reverse=True)[:10]
        idcg = _dcg(ideal_grades)
        ndcg_scores.append(_dcg(actual_grades) / idcg if idcg > 0 else 0.0)

    def _avg(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    return EvaluationReport(
        recall_at_k={k: _avg(v) for k, v in recall_hits.items()},
        precision_at_k={k: _avg(v) for k, v in precision_hits.items()},
        mrr=_avg(reciprocal_ranks),
        ndcg_at_10=_avg(ndcg_scores),
        p50_latency_ms=statistics.median(latencies_ms) if latencies_ms else 0.0,
        p95_latency_ms=(
            statistics.quantiles(latencies_ms, n=20)[18]
            if len(latencies_ms) >= 20
            else max(latencies_ms, default=0.0)
        ),
        num_questions=len(corpus),
        num_answerable=len(corpus) - unanswerable_total,
        unanswerable_top1_false_positive_rate=(
            unanswerable_false_positives / unanswerable_total if unanswerable_total else 0.0
        ),
    )
