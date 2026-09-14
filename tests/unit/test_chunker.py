"""Chunking tests.

These pin the failure that actually happens in production: a chunk boundary
landing inside a section, so a late-penalty rule ends up attached to an
assessment weighting and the assistant confidently states the wrong thing.
"""

from __future__ import annotations

from app.ingestion.chunker import chunk_document, estimate_tokens
from app.ingestion.parser import Page, ParsedDocument

SYLLABUS = """\
ASSESSMENT
Assignment 1 is worth 20 per cent and is due in Week 4.
Assignment 2 is worth 30 per cent and is due in Week 9.
The final examination is worth 50 per cent.

LATE PENALTIES
Work submitted late loses 5 per cent per day, to a maximum of 10 days.
After 10 days the submission receives zero.
"""


def _doc(text: str, pages: int = 1) -> ParsedDocument:
    return ParsedDocument(
        pages=[Page(number=i + 1, text=text) for i in range(pages)],
        page_count=pages,
        detected_type="txt",
    )


def test_sections_split_on_headings() -> None:
    chunks = chunk_document(_doc(SYLLABUS), max_tokens=700, overlap_tokens=100)
    sections = {c.section for c in chunks}
    assert "ASSESSMENT" in sections
    assert "LATE PENALTIES" in sections


def test_late_penalties_never_merge_into_assessment() -> None:
    """The whole reason for section-aware chunking.

    If these merge, a passage reads as though the 5-per-cent-per-day penalty is
    part of the assessment weighting -- and the assistant will say so, with a
    citation, which makes it worse rather than better.
    """
    chunks = chunk_document(_doc(SYLLABUS), max_tokens=700, overlap_tokens=100)
    assessment = [c for c in chunks if c.section == "ASSESSMENT"]
    assert assessment
    assert all("5 per cent per day" not in c.text for c in assessment)


def test_windows_respect_the_token_budget() -> None:
    long_text = "The tutorial covers linked lists and their traversal. " * 400
    chunks = chunk_document(_doc(long_text), max_tokens=200, overlap_tokens=40)
    assert len(chunks) > 1
    # Sentence packing can overshoot by at most the final sentence.
    assert all(c.token_count <= 260 for c in chunks)


def test_windows_overlap_so_context_is_not_lost_at_the_seam() -> None:
    sentences = [f"Week {i} covers topic number {i} in detail." for i in range(200)]
    chunks = chunk_document(_doc(" ".join(sentences)), max_tokens=150, overlap_tokens=50)
    assert len(chunks) > 2
    first_tail = set(chunks[0].text.split()[-12:])
    second_head = set(chunks[1].text.split()[:40])
    assert first_tail & second_head, "consecutive chunks share no text"


def test_ordinals_are_contiguous_across_pages() -> None:
    chunks = chunk_document(_doc(SYLLABUS, pages=3), max_tokens=700, overlap_tokens=100)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_page_numbers_survive_chunking() -> None:
    # Page numbers reach the citation, so a teacher can open the PDF at the
    # right page. Losing them here silently degrades every citation.
    chunks = chunk_document(_doc(SYLLABUS, pages=2), max_tokens=700, overlap_tokens=100)
    assert {c.page_from for c in chunks} == {1, 2}


def test_short_document_is_one_chunk_per_section() -> None:
    chunks = chunk_document(
        _doc("SCHEDULE\nWeek 1 is an introduction."), max_tokens=700, overlap_tokens=100
    )
    assert len(chunks) == 1


def test_overlap_must_be_smaller_than_the_budget() -> None:
    import pytest

    with pytest.raises(ValueError, match="overlap_tokens"):
        chunk_document(_doc(SYLLABUS), max_tokens=100, overlap_tokens=100)


def test_token_estimate_is_monotonic() -> None:
    assert estimate_tokens("short") < estimate_tokens("short " * 100)
