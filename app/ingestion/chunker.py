"""Chunking: section-aware first, then token-budgeted windows with overlap.

Why not fixed-size splitting: a syllabus is a form. Splitting mid-way through
"Assessment" and gluing the tail onto "Late penalties" produces a chunk that
reads as if the penalty applies to the assessment weighting. Cutting on headings
first, and only windowing *inside* a section, keeps each chunk self-contained.

Token counts here use a fast heuristic (~3.8 chars/token for English prose).
That is fine for sizing chunks. Where an exact count matters -- checking an
assembled prompt against a context window -- the agent asks the provider.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ingestion.parser import Page, ParsedDocument, find_sections

CHARS_PER_TOKEN = 3.8


@dataclass(frozen=True)
class TextChunk:
    ordinal: int
    text: str
    token_count: int
    page_from: int
    page_to: int
    section: str | None


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / CHARS_PER_TOKEN))


def chunk_document(
    parsed: ParsedDocument, *, max_tokens: int, overlap_tokens: int
) -> list[TextChunk]:
    if overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be smaller than max_tokens")

    chunks: list[TextChunk] = []
    ordinal = 0
    for section_text, section_name, page_from, page_to in _sections(parsed.pages):
        for body in _window(section_text, max_tokens, overlap_tokens):
            chunks.append(
                TextChunk(
                    ordinal=ordinal,
                    text=body,
                    token_count=estimate_tokens(body),
                    page_from=page_from,
                    page_to=page_to,
                    section=section_name,
                )
            )
            ordinal += 1
    return chunks


def _sections(pages: list[Page]) -> list[tuple[str, str | None, int, int]]:
    """Split each page on headings. Returns (text, heading, page_from, page_to)."""
    out: list[tuple[str, str | None, int, int]] = []
    for page in pages:
        if not page.text.strip():
            continue
        headings = find_sections(page.text)
        if not headings:
            out.append((page.text, None, page.number, page.number))
            continue

        # Text before the first heading still belongs to the document.
        if headings[0][0] > 0:
            preamble = page.text[: headings[0][0]].strip()
            if preamble:
                out.append((preamble, None, page.number, page.number))

        for i, (offset, heading) in enumerate(headings):
            end = headings[i + 1][0] if i + 1 < len(headings) else len(page.text)
            body = page.text[offset:end].strip()
            if body:
                out.append((body, heading, page.number, page.number))
    return out


def _window(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    """Pack whole sentences into token-budgeted windows with a sentence overlap."""
    if estimate_tokens(text) <= max_tokens:
        return [text]

    sentences = _split_sentences(text)
    windows: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for sentence in sentences:
        sentence_tokens = estimate_tokens(sentence)
        if current and current_tokens + sentence_tokens > max_tokens:
            windows.append(" ".join(current))
            # Carry back whole sentences until the overlap budget is met, so a
            # boundary never lands inside a due date or a weighting.
            carry: list[str] = []
            carried = 0
            for prev in reversed(current):
                prev_tokens = estimate_tokens(prev)
                if carried + prev_tokens > overlap_tokens:
                    break
                carry.insert(0, prev)
                carried += prev_tokens
            current = carry
            current_tokens = carried
        current.append(sentence)
        current_tokens += sentence_tokens

    if current:
        windows.append(" ".join(current))
    return windows


_SENTENCE_END = re.compile(r"(?<=[.!?:])\s+(?=[A-Z0-9])|\n+")


def _split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_END.split(text) if p and p.strip()]
    return parts or [text]
