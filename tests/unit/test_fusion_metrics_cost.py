"""Fusion, eval metrics and cost accounting — all pure functions, no network."""

from __future__ import annotations

from app.evals import metrics
from app.obs.cost import PRICING, Usage, is_priced
from app.rag.fusion import ranked_ids, reciprocal_rank_fusion

# ── Reciprocal rank fusion ──────────────────────────────────────────────────


def test_agreement_between_retrievers_beats_a_single_top_hit() -> None:
    """The property that makes RRF worth having.

    'b' is second and first; 'a' is first and absent. Agreement across two
    independent retrievers is a stronger signal than one confident vote.
    """
    fused = reciprocal_rank_fusion({"dense": ["a", "b", "c"], "keyword": ["b", "d", "e"]})
    assert ranked_ids(fused)[0] == "b"


def test_fusion_records_which_retrievers_contributed() -> None:
    # 'dense only' vs 'both agreed' is what makes retrieval debuggable from the
    # UI instead of from logs.
    fused = reciprocal_rank_fusion({"dense": ["a"], "keyword": ["a"]})
    _score, sources = fused["a"]
    assert sorted(sources) == ["dense", "keyword"]


def test_fusion_needs_no_score_normalisation() -> None:
    # Only rank is used, so wildly different score scales cannot skew the result.
    fused = reciprocal_rank_fusion({"dense": ["x", "y"], "keyword": ["y", "x"]})
    assert abs(fused["x"][0] - fused["y"][0]) < 1e-9


def test_fusion_of_nothing_is_nothing() -> None:
    assert reciprocal_rank_fusion({"dense": [], "keyword": []}) == {}


def test_ranked_ids_respects_a_limit() -> None:
    fused = reciprocal_rank_fusion({"dense": ["a", "b", "c", "d"]})
    assert len(ranked_ids(fused, limit=2)) == 2


# ── Retrieval metrics ───────────────────────────────────────────────────────


def test_recall_counts_only_expected_passages() -> None:
    assert metrics.recall_at_k(["a", "b", "x"], ["a", "b"]) == 1.0
    assert metrics.recall_at_k(["a"], ["a", "b"]) == 0.5
    assert metrics.recall_at_k(["x"], ["a", "b"]) == 0.0


def test_mrr_rewards_ranking_the_answer_first() -> None:
    # This is the metric reranking is supposed to move.
    assert metrics.mean_reciprocal_rank(["a", "b"], ["a"]) == 1.0
    assert metrics.mean_reciprocal_rank(["b", "a"], ["a"]) == 0.5
    assert metrics.mean_reciprocal_rank(["x", "y"], ["a"]) == 0.0


# ── Answer metrics ──────────────────────────────────────────────────────────


def test_a_hallucinated_citation_index_scores_zero() -> None:
    # A citation pointing at a passage that does not exist looks like
    # provenance and is not -- worse than no citation at all.
    assert metrics.citation_validity("See [7].", n_context_blocks=3) == 0.0
    assert metrics.citation_validity("See [1] and [2].", n_context_blocks=3) == 1.0


def test_an_uncited_answer_scores_zero() -> None:
    assert metrics.citation_validity("No brackets here.", n_context_blocks=3) == 0.0


def test_fact_coverage_is_literal_on_purpose() -> None:
    assert metrics.fact_coverage("Assignment 1 is worth 20 per cent", ["20 per cent"]) == 1.0
    # A paraphrased weighting is a miss. That is the point.
    assert metrics.fact_coverage("Assignment 1 is worth a fifth", ["20 per cent"]) == 0.0


def test_refusal_detection() -> None:
    assert metrics.looks_like_refusal("The course materials do not cover that.")
    assert not metrics.looks_like_refusal("Assignment 2 is due in Week 9.")


def test_percentiles_handle_small_samples() -> None:
    assert metrics.percentile([], 95) == 0.0
    assert metrics.percentile([42.0], 95) == 42.0


# ── Cost ────────────────────────────────────────────────────────────────────


def test_cached_tokens_are_billed_at_the_discounted_rate() -> None:
    uncached = Usage(model="gpt-4o", input_tokens=100_000, output_tokens=1_000)
    cached = Usage(
        model="gpt-4o", input_tokens=100_000, output_tokens=1_000, cached_input_tokens=90_000
    )
    assert cached.cost_usd < uncached.cost_usd


def test_cache_hit_rate() -> None:
    usage = Usage(model="gpt-4o", input_tokens=1_000, output_tokens=10, cached_input_tokens=750)
    assert usage.cache_hit_rate == 0.75


def test_zero_usage_is_free_not_a_crash() -> None:
    usage = Usage(model="gpt-4o", input_tokens=0, output_tokens=0)
    assert usage.cost_usd == 0.0
    assert usage.cache_hit_rate == 0.0


def test_an_unknown_model_is_priced_not_free() -> None:
    """Pricing an unknown model at zero hides a cost regression -- the bill goes
    up and every dashboard still reads $0.00."""
    usage = Usage(model="some-model-released-next-week", input_tokens=1_000_000, output_tokens=0)
    assert usage.cost_usd > 0
    assert not is_priced("some-model-released-next-week")


def test_both_providers_are_priced() -> None:
    # The provider benchmark compares cost per run, so a missing price on
    # either side makes the comparison meaningless.
    assert is_priced("gpt-4o")
    assert is_priced("claude-opus-5")


def test_embedding_models_have_no_output_cost() -> None:
    _in, out, _cached = PRICING["text-embedding-3-small"]
    assert out == 0.0
