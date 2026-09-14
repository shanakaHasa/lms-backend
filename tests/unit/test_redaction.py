"""Redaction, in both directions.

Secrets and student personal information must be removed; ordinary diagnostic
content must survive. A scrubber that eats everything gets switched off, which
is worse than not having one.
"""

from __future__ import annotations

from app.core.redaction import REDACTED, is_sensitive_key, redact_processor, scrub


def test_api_keys_are_removed() -> None:
    assert "sk-abc123def456ghi789" not in scrub("using key sk-abc123def456ghi789")
    assert "sk-ant-abc123def456ghi" not in scrub("key sk-ant-abc123def456ghi789")


def test_bearer_tokens_and_jwts_are_removed() -> None:
    assert "abcdef123456ghijkl" not in scrub("Authorization: Bearer abcdef123456ghijkl")
    jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJlX2hlcmU"
    assert jwt not in scrub(f"token {jwt} rejected")


def test_student_contact_details_are_removed() -> None:
    out = scrub("contact jane.doe@school.edu.au on 0412 345 678, born 14/03/2008")
    assert "jane.doe@school.edu.au" not in out
    assert "0412" not in out
    assert "14/03/2008" not in out


def test_student_numbers_are_removed() -> None:
    assert "S00142" not in scrub("enrolled student S00142 in COMP201")


def test_sensitive_field_names_are_redacted_wholesale() -> None:
    for key in (
        "password",
        "api_key",
        "OPENAI_API_KEY",
        "date_of_birth",
        "phone",
        "home_address",
        "guardian_email",
    ):
        assert is_sensitive_key(key), key


def test_ordinary_fields_are_not_sensitive() -> None:
    for key in ("tenant_id", "course_code", "status", "duration_ms", "student_id"):
        assert not is_sensitive_key(key), key


def test_diagnostic_content_survives() -> None:
    # If this fails the scrubber is too aggressive and someone will disable it.
    text = "retrieved 8 passages for COMP201 in 142ms, reranked to 4"
    assert scrub(text) == text


def test_course_codes_are_not_mistaken_for_student_numbers() -> None:
    # COMP201 has too few digits to match the student-number pattern. If this
    # regresses, every course code in every log line becomes unreadable.
    assert "COMP201" in scrub("course COMP201 has 30 enrolments")


def test_nested_payloads_are_scrubbed() -> None:
    out = scrub({"student": {"email": "a@b.test", "name": "Jane"}, "count": 3})
    assert out["student"]["email"] != "a@b.test"
    assert out["student"]["name"] == "Jane"  # names are unavoidable in an LMS
    assert out["count"] == 3


def test_recursion_is_depth_capped() -> None:
    deep: dict = {}
    node = deep
    for _ in range(50):
        node["next"] = {}
        node = node["next"]
    assert scrub(deep) is not None


def test_the_processor_redacts_the_whole_event() -> None:
    out = redact_processor(None, "info", {"event": "chat", "api_key": "x", "course": "COMP201"})
    assert out["api_key"] == REDACTED
    assert out["course"] == "COMP201"
