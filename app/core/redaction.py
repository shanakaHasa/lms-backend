"""Redaction for logs and traces.

Two jobs, because this service handles two kinds of sensitive data:

  * **Secrets** — API keys, bearer tokens. Same treatment as auth-backend.
  * **Student personal information** — names are unavoidable in an LMS, but
    contact details, dates of birth and student numbers have no business in a
    log line or in a Langfuse trace.

Applied as a structlog processor, so it covers every log call rather than
depending on each caller remembering. `redact_payload` is the same function
applied to anything crossing the trust boundary into Langfuse.

This is a safety net, not de-identification: regex redaction will miss an
identifier written in an unusual form. The primary control is not putting
student records into traces in the first place.
"""

from __future__ import annotations

import re
from collections.abc import MutableMapping
from typing import Any

SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "password",
    "secret",
    "token",
    "authorization",
    "cookie",
    "credential",
    "api_key",
    "apikey",
    "private_key",
    # Student identifiers, by field name.
    "date_of_birth",
    "dob",
    "phone",
    "address",
    "guardian",
)

REDACTED = "[REDACTED]"
MAX_VALUE_CHARS = 4000

# Credentials that arrived inside a free-text string rather than a field.
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-+/=]{8,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b")
_OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")
_ANTHROPIC_KEY = re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}")

# Student personal information in free text.
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE = re.compile(r"\b(?:\+?61\s?|0)[2-478](?:[ -]?\d){8}\b")
_DOB = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}\b")
# Student numbers as issued by the seed data and most institutions: a letter
# prefix and digits, e.g. S00142.
_STUDENT_NUMBER = re.compile(r"\b[A-Z]{1,3}\d{5,10}\b")

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_ANTHROPIC_KEY, REDACTED),
    (_OPENAI_KEY, REDACTED),
    (_JWT, REDACTED),
    (_EMAIL, "[EMAIL]"),
    (_PHONE, "[PHONE]"),
    (_DOB, "[DOB]"),
    (_STUDENT_NUMBER, "[STUDENT_NO]"),
)


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def scrub_text(value: str) -> str:
    value = _BEARER.sub(r"\1 " + REDACTED, value)
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    return value[:MAX_VALUE_CHARS]


def scrub(value: Any, *, _depth: int = 0) -> Any:
    """Recursively redact. Depth-capped so a deeply nested payload cannot turn
    a log call into a hang."""
    if _depth > 6:
        return REDACTED
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {
            k: REDACTED if is_sensitive_key(str(k)) else scrub(v, _depth=_depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [scrub(v, _depth=_depth + 1) for v in value]
    return value


def redact_payload(value: Any, *, enabled: bool = True) -> Any:
    """Used on anything leaving the trust boundary -- Langfuse traces, eval
    artefacts committed to the repo."""
    return scrub(value) if enabled else value


def redact_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor, applied to every event."""
    return {k: REDACTED if is_sensitive_key(k) else scrub(v) for k, v in event_dict.items()}
