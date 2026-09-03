from __future__ import annotations

import math
from collections.abc import Awaitable, Callable

import pytest

from ai_brain.retrieval.evaluation import (
    DEFAULT_CORPUS_DIR,
    Question,
    RelevanceJudgment,
    load_corpus,
    run_evaluation,
)


def _stub_search(
    ranking_by_question: dict[str, list[str]],
) -> Callable[[str], Awaitable[list[str]]]:
    async def search_fn(query_text: str) -> list[str]:
        return ranking_by_question[query_text]

    return search_fn


async def test_perfect_ranking_scores_maximally() -> None:
    corpus = [
        Question(
            id="q1",
            text="q1",
            judgments=[
                RelevanceJudgment(note_path="a.md", grade=2),
                RelevanceJudgment(note_path="b.md", grade=1),
            ],
        )
    ]
    search_fn = _stub_search({"q1": ["a.md", "b.md", "c.md"]})

    report = await run_evaluation(corpus, search_fn)

    assert report.recall_at_k[3] == 1.0
    assert report.precision_at_k[3] == pytest.approx(2 / 3)
    assert report.mrr == 1.0
    assert report.ndcg_at_10 == pytest.approx(1.0)


async def test_no_relevant_docs_found_scores_zero() -> None:
    corpus = [
        Question(id="q1", text="q1", judgments=[RelevanceJudgment(note_path="a.md", grade=1)])
    ]
    search_fn = _stub_search({"q1": ["x.md", "y.md", "z.md"]})

    report = await run_evaluation(corpus, search_fn)

    assert report.recall_at_k[3] == 0.0
    assert report.precision_at_k[3] == 0.0
    assert report.mrr == 0.0
    assert report.ndcg_at_10 == 0.0


async def test_partial_match_hand_computed_values() -> None:
    # relevant = {a.md: grade 2, b.md: grade 1}; ranked = [a.md, c.md, b.md]
    corpus = [
        Question(
            id="q1",
            text="q1",
            judgments=[
                RelevanceJudgment(note_path="a.md", grade=2),
                RelevanceJudgment(note_path="b.md", grade=1),
            ],
        )
    ]
    search_fn = _stub_search({"q1": ["a.md", "c.md", "b.md"]})

    report = await run_evaluation(corpus, search_fn)

    assert report.recall_at_k[3] == pytest.approx(1.0)  # both relevant docs in top 3
    assert report.precision_at_k[3] == pytest.approx(2 / 3)
    assert report.mrr == pytest.approx(1.0)  # first relevant (a.md) at rank 1

    dcg = 2 / math.log2(2) + 0 / math.log2(3) + 1 / math.log2(4)
    idcg = 2 / math.log2(2) + 1 / math.log2(3)
    assert report.ndcg_at_10 == pytest.approx(dcg / idcg)


async def test_unanswerable_question_excluded_from_recall_but_tracked_separately() -> None:
    corpus = [
        Question(id="q1", text="q1", judgments=[]),  # deliberately unanswerable
        Question(
            id="q2", text="q2", judgments=[RelevanceJudgment(note_path="a.md", grade=1)]
        ),
    ]
    search_fn = _stub_search({"q1": ["confident-wrong-hit.md"], "q2": ["a.md"]})

    report = await run_evaluation(corpus, search_fn)

    assert report.num_questions == 2
    assert report.num_answerable == 1
    assert report.unanswerable_top1_false_positive_rate == 1.0  # q1 returned a hit anyway
    # q1 must not pollute recall/precision/MRR/nDCG, which are computed only over q2
    assert report.recall_at_k[3] == 1.0
    assert report.mrr == 1.0


async def test_unanswerable_question_with_empty_result_is_not_a_false_positive() -> None:
    corpus = [Question(id="q1", text="q1", judgments=[])]
    search_fn = _stub_search({"q1": []})

    report = await run_evaluation(corpus, search_fn)

    assert report.unanswerable_top1_false_positive_rate == 0.0


async def test_latency_is_recorded_and_non_negative() -> None:
    corpus = [
        Question(id="q1", text="q1", judgments=[RelevanceJudgment(note_path="a.md", grade=1)])
    ]
    search_fn = _stub_search({"q1": ["a.md"]})

    report = await run_evaluation(corpus, search_fn)

    assert report.p50_latency_ms >= 0.0
    assert report.p95_latency_ms >= report.p50_latency_ms


def test_load_corpus_parses_the_real_starter_corpus() -> None:
    corpus = load_corpus(DEFAULT_CORPUS_DIR)

    assert len(corpus) == 17
    unanswerable = [q for q in corpus if not q.judgments]
    assert len(unanswerable) == 3
    cross_note = [q for q in corpus if len(q.judgments) > 1]
    assert len(cross_note) >= 1
    for question in corpus:
        for judgment in question.judgments:
            note_path = DEFAULT_CORPUS_DIR / "vault" / judgment.note_path
            assert note_path.exists(), f"{judgment.note_path} referenced by {question.id}"
