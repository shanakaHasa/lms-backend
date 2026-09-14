"""Reciprocal Rank Fusion.

Combines rankings from retrievers whose scores are not comparable. Cosine
similarity from a vector index and `ts_rank_cd` from Postgres live on different
scales, and those scales shift as the corpus grows -- so any fixed weighting
drifts silently. RRF uses only *rank*, which needs no normalisation and no
re-tuning.

k=60 is the value from the original Cormack et al. paper and is a reasonable
default. It is a tunable, and the eval suite measures whether changing it helps
rather than leaving it to taste.
"""

from __future__ import annotations

RRF_K = 60


def reciprocal_rank_fusion(
    rankings: dict[str, list[str]], k: int = RRF_K
) -> dict[str, tuple[float, list[str]]]:
    """rankings: retriever name -> ids, best first.

    Returns id -> (fused_score, which retrievers found it). Carrying the
    provenance matters: "dense only" versus "both agreed" is the difference
    between a lucky embedding match and a real one, and it is what makes
    retrieval debuggable from the UI instead of from logs.
    """
    fused: dict[str, float] = {}
    sources: dict[str, list[str]] = {}
    for retriever_name, ids in rankings.items():
        for rank, item_id in enumerate(ids, start=1):
            fused[item_id] = fused.get(item_id, 0.0) + 1.0 / (k + rank)
            sources.setdefault(item_id, []).append(retriever_name)
    return {item_id: (score, sources[item_id]) for item_id, score in fused.items()}


def ranked_ids(fused: dict[str, tuple[float, list[str]]], limit: int | None = None) -> list[str]:
    """Fused ids, best first."""
    ordered = sorted(fused, key=lambda item_id: fused[item_id][0], reverse=True)
    return ordered[:limit] if limit is not None else ordered
