"""Deterministic evaluation metrics.

Everything here is arithmetic on ids and strings -- no model calls. They run in
milliseconds and cost nothing, so they gate the expensive judge: a run that
fails retrieval recall does not need grading for faithfulness.
"""

from __future__ import annotations

import re
import statistics


def recall_at_k(retrieved_ids: list[str], expected_ids: list[str]) -> float:
    """Fraction of the expected passages that made it into the context."""
    if not expected_ids:
        return 1.0
    hit = len(set(retrieved_ids) & set(expected_ids))
    return hit / len(set(expected_ids))


def mean_reciprocal_rank(retrieved_ids: list[str], expected_ids: list[str]) -> float:
    """1/rank of the first relevant passage. Rewards putting the answer first,
    which is what reranking is supposed to improve."""
    if not expected_ids:
        return 1.0
    expected = set(expected_ids)
    for rank, item_id in enumerate(retrieved_ids, start=1):
        if item_id in expected:
            return 1.0 / rank
    return 0.0


_CITATION = re.compile(r"\[(\d{1,2})\]")


def citation_validity(answer: str, n_context_blocks: int) -> float:
    """Fraction of bracketed indices that point at a passage that exists.

    A hallucinated citation number is worse than no citation: it looks like
    provenance and is not. Cheap to check, and it catches it every time.
    """
    indices = [int(m) for m in _CITATION.findall(answer)]
    if not indices:
        return 0.0
    valid = [i for i in indices if 1 <= i <= n_context_blocks]
    return len(valid) / len(indices)


def cited_indices(answer: str) -> set[int]:
    return {int(m) for m in _CITATION.findall(answer)}


def fact_coverage(answer: str, expected_facts: list[str]) -> float:
    """Substring coverage of strings that must appear verbatim.

    Deliberately literal. Due dates, weightings and course codes are exactly the
    values that must not be paraphrased, so an exact-match check is the right
    instrument even though it looks crude.
    """
    if not expected_facts:
        return 1.0
    normalised = answer.lower()
    hits = sum(1 for fact in expected_facts if fact.lower() in normalised)
    return hits / len(expected_facts)


_REFUSAL_MARKERS = (
    "do not contain",
    "does not contain",
    "no documents",
    "not in the documents",
    "could not find",
    "cannot find",
    "no information",
    "not covered",
    "do not cover",
)


def looks_like_refusal(answer: str) -> bool:
    lowered = answer.lower()
    return any(marker in lowered for marker in _REFUSAL_MARKERS)


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = min(round(p / 100 * (len(ordered) - 1)), len(ordered) - 1)
    return ordered[index]


def mean(values: list[float]) -> float:
    return round(statistics.fmean(values), 4) if values else 0.0
