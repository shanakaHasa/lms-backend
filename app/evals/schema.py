"""Eval dataset and grading types.

Domain-neutral on purpose: the golden set is data, and swapping it should not
mean touching the harness.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class EvalCase(BaseModel):
    """One golden case.

    `expected_chunk_ids` grades retrieval directly; without it only the
    generation metrics run.

    `must_refuse` cases are the ones that keep the assistant honest -- questions
    the corpus genuinely cannot answer, where a confident answer is the failure.
    A suite without them rewards guessing.

    `expected_proposal` is the LMS-specific addition: for a case like "enrol
    Jane in COMP201", the assistant must produce exactly one proposal with the
    right arguments and must NOT execute it.
    """

    id: str
    question: str
    expected_answer: str | None = None
    expected_chunk_ids: list[str] = Field(default_factory=list)
    # Strings that must appear VERBATIM. Due dates, weightings and course codes
    # are exactly the values that must not be paraphrased.
    expected_facts: list[str] = Field(default_factory=list)
    must_refuse: bool = False
    expected_proposal: dict[str, object] | None = None
    expects_no_proposal: bool = False
    doc_type: str | None = None
    tags: list[str] = Field(default_factory=list)
    split: Literal["train", "validation", "test"] = "test"


class Judgement(BaseModel):
    """Structured output from the LLM judge.

    Schema-enforced rather than "reply with JSON", so a malformed grade cannot
    silently become a zero and drag a run's mean down.
    """

    score: float = Field(ge=0.0, le=1.0)
    reasoning: str
    unsupported_claims: list[str] = Field(default_factory=list)


class CaseScores(BaseModel):
    case_id: str
    faithfulness: float | None = None
    answer_relevance: float | None = None
    citation_validity: float | None = None
    fact_coverage: float | None = None
    retrieval_recall: float | None = None
    retrieval_mrr: float | None = None
    refusal_correct: bool | None = None
    proposal_correct: bool | None = None
    latency_ms: int = 0
    cost_usd: float = 0.0
    answer: str = ""
    notes: list[str] = Field(default_factory=list)


class RunSummary(BaseModel):
    dataset: str
    provider: str
    model: str
    prompt_version: str
    n_cases: int
    means: dict[str, float]
    total_cost_usd: float
    p50_latency_ms: int
    p95_latency_ms: int
    passed: bool
    failures: list[str]
