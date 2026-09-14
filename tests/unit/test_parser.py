"""Parser and heading detection.

Heading detection decides where chunks are cut, so an over-eager pattern is not
a cosmetic problem — it shreds documents into fragments that retrieve badly.
"""

from __future__ import annotations

import pytest

from app.core.errors import ValidationFailed
from app.ingestion.parser import detect_type, find_sections, parse


def _headings(text: str) -> list[str]:
    return [h for _offset, h in find_sections(text)]


def test_all_caps_headings_are_found() -> None:
    assert "ASSESSMENT" in _headings("ASSESSMENT\nAssignment 1 is worth 20 per cent.")


def test_titled_sections_are_found() -> None:
    for heading in ("Learning Outcomes", "Late Penalties", "Academic Integrity"):
        assert heading in _headings(f"{heading}\nSome body text follows here.")


def test_numbered_headings_are_found() -> None:
    assert _headings("1.2 Assessment Overview\nBody text.") == ["1.2 Assessment Overview"]


def test_week_headings_are_found() -> None:
    for heading in ("Week 3", "Week 3 - Linked Lists", "Module 2: Recursion"):
        assert _headings(f"{heading}\nBody text.") == [heading]


def test_a_sentence_starting_with_week_is_not_a_heading() -> None:
    """The regression this file exists for.

    `Week\\s+\\d+.*` matches any line beginning "Week 1 ...", which turns
    ordinary syllabus prose into headings and shreds the chunks. Sentence
    punctuation is what distinguishes the two.
    """
    assert _headings("Week 1 is an introduction to the course.") == []
    assert _headings("Week 2 covers arrays, lists and their trade-offs.") == []


def test_prose_is_not_mistaken_for_headings() -> None:
    prose = (
        "Students should submit their work through the portal.\n"
        "Extensions are considered on a case-by-case basis.\n"
    )
    assert _headings(prose) == []


# ── Type detection ──────────────────────────────────────────────────────────


def test_type_is_detected_from_the_mime_type() -> None:
    assert detect_type("application/pdf", "anything") == "pdf"
    assert detect_type("text/markdown", "syllabus.md") == "txt"


def test_type_falls_back_to_the_extension() -> None:
    # Browsers send application/octet-stream more often than anyone expects.
    assert detect_type("application/octet-stream", "syllabus.pdf") == "pdf"


def test_an_unsupported_type_is_rejected_with_the_supported_list() -> None:
    with pytest.raises(ValidationFailed) as exc:
        detect_type("image/png", "scan.png")
    assert "supported" in exc.value.detail


# ── Text parsing ────────────────────────────────────────────────────────────


def test_plain_text_parses_to_one_page() -> None:
    parsed = parse(b"COURSE OVERVIEW\nAn introduction.", "text/plain", "s.txt")
    assert parsed.page_count == 1
    assert "COURSE OVERVIEW" in parsed.pages[0].text


def test_whitespace_is_normalised_without_losing_paragraphs() -> None:
    parsed = parse(b"A\r\n\r\n\r\n\r\nB", "text/plain", "s.txt")
    # Runs of blank lines collapse to one, so paragraph structure survives but
    # the chunker does not see phantom sections.
    assert parsed.pages[0].text == "A\n\nB"


def test_an_empty_document_is_reported_as_empty() -> None:
    assert parse(b"   \n  ", "text/plain", "s.txt").is_empty
