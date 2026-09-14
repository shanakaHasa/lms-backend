"""LMS schema invariants.

The comments in the model modules claim the database enforces certain
properties. These hold those claims to account, against metadata, so they need
no database.

Each corresponds to a way this system could actually go wrong — and several
matter more than usual because **the assistant proposes writes**, so a
model-generated argument that slipped through would land in these tables.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

import app.models  # noqa: F401  -- importing registers every table
from app.core.db import Base

TABLES = Base.metadata.tables

EXPECTED_TABLES = {
    "students",
    "courses",
    "enrolments",
    "course_materials",
    "material_chunks",
    "ingestion_jobs",
    "conversations",
    "messages",
    "action_proposals",
    "audit_events",
}


def test_every_expected_table_exists() -> None:
    assert set(TABLES) == EXPECTED_TABLES


def _uniques(table: str) -> set[tuple[str, ...]]:
    return {
        tuple(c.name for c in constraint.columns)
        for constraint in TABLES[table].constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _index(table: str, name: str):  # type: ignore[no-untyped-def]
    return next(ix for ix in TABLES[table].indexes if ix.name == name)


def _where(index) -> str:  # type: ignore[no-untyped-def]
    return str(index.dialect_options["postgresql"]["where"])


# ── The service boundary ────────────────────────────────────────────────────


def test_nothing_references_the_auth_service() -> None:
    """The boundary that makes this a separate service.

    Identity lives in another database. `tenant_id`, `created_by`,
    `teacher_user_id` and `approved_by_user_id` are opaque strings from a
    verified token — a foreign key to any of them would couple the two
    databases and make the split cosmetic.
    """
    for name, table in TABLES.items():
        for fk in table.foreign_keys:
            referenced = fk.target_fullname.split(".")[0]
            assert referenced in EXPECTED_TABLES, (
                f"{name} has a foreign key to {referenced}, which is outside this service"
            )


def test_identity_columns_are_plain_strings() -> None:
    from sqlalchemy import String

    for table, column in (
        ("students", "tenant_id"),
        ("students", "created_by"),
        ("courses", "teacher_user_id"),
        ("action_proposals", "proposed_by_user_id"),
        ("audit_events", "actor_id"),
    ):
        assert isinstance(TABLES[table].columns[column].type, String)


# ── Enrolments: the table the agent writes to ───────────────────────────────


def test_a_student_cannot_be_enrolled_twice_in_one_course() -> None:
    """Structural, not merely unlikely.

    The assistant proposes enrolments, so a retried or duplicated proposal must
    be incapable of producing a second row rather than merely unlikely to.
    """
    assert ("student_id", "course_id") in _uniques("enrolments")


def test_enrolments_validate_both_sides_against_the_same_tenant() -> None:
    """The highest-value constraint in this schema.

    An enrolment is only valid if the student AND the course belong to the
    tenant on the row, so joining a student in one institution to a course in
    another is a foreign-key violation rather than something a missing WHERE
    clause can cause.
    """
    composite = [
        fk
        for fk in TABLES["enrolments"].constraints
        if isinstance(fk, ForeignKeyConstraint) and len(fk.columns) == 2
    ]
    assert len(composite) == 2, "expected composite FKs to both students and courses"

    for fk in composite:
        local = {c.name for c in fk.columns}
        assert "tenant_id" in local, f"{fk.name} does not carry tenant_id"
        referred = {element.target_fullname for element in fk.elements}
        assert any(name.endswith(".tenant_id") for name in referred)


def test_students_and_courses_expose_the_composite_unique() -> None:
    # Postgres refuses a foreign key into (id, tenant_id) without a matching
    # unique constraint, so the guarantee above is not merely weaker without
    # these -- it is impossible to declare.
    assert ("id", "tenant_id") in _uniques("students")
    assert ("id", "tenant_id") in _uniques("courses")


# ── Students ────────────────────────────────────────────────────────────────


def test_student_identifiers_are_unique_per_tenant_among_live_rows() -> None:
    for index_name, columns in (
        ("uq_students_tenant_number", ["tenant_id", "student_number"]),
        ("uq_students_tenant_email", ["tenant_id", "email_normalized"]),
    ):
        index = _index("students", index_name)
        assert index.unique
        assert [c.name for c in index.columns] == columns
        # Live rows only, so an identifier is reusable after a deletion.
        assert "deleted_at IS NULL" in _where(index)


def test_students_are_soft_deleted() -> None:
    assert TABLES["students"].columns["deleted_at"].nullable


# ── Materials and chunks ────────────────────────────────────────────────────


def test_a_material_need_not_belong_to_a_course() -> None:
    # NULL means institution-wide. An academic-integrity policy belongs to no
    # single course, and the assistant must still be able to find it.
    assert TABLES["course_materials"].columns["course_id"].nullable


def test_reuploading_the_same_file_is_a_no_op() -> None:
    # Without this, an accidental second upload doubles every retrieval result
    # for that document.
    assert ("tenant_id", "sha256") in _uniques("course_materials")


def test_the_search_vector_is_generated_not_maintained() -> None:
    """Postgres keeps `tsv` in step with `text` itself.

    A trigger can be dropped, disabled, or bypassed by a bulk load, and the
    symptom is keyword search silently disagreeing with the passage the model
    was shown. A generated column cannot drift.
    """
    tsv = TABLES["material_chunks"].columns["tsv"]
    assert tsv.computed is not None
    assert tsv.computed.persisted
    assert "to_tsvector" in str(tsv.computed.sqltext)


def test_the_search_vector_is_gin_indexed() -> None:
    index = _index("material_chunks", "ix_material_chunks_tsv")
    assert index.dialect_options["postgresql"]["using"] == "gin"


def test_chunks_are_ordered_within_a_material() -> None:
    assert ("material_id", "ordinal") in _uniques("material_chunks")


def test_unembedded_chunks_are_findable() -> None:
    # The recoverable state when ingestion dies between Postgres and the vector
    # index. Partial, because in a healthy system this set is empty.
    assert "embedded_at IS NULL" in _where(
        _index("material_chunks", "ix_material_chunks_unembedded")
    )


# ── The queue ───────────────────────────────────────────────────────────────


def test_redelivery_cannot_duplicate_chunks() -> None:
    # Delivery is at-least-once by design; this is what makes that safe.
    assert ("idempotency_key",) in _uniques("ingestion_jobs")


def test_the_queue_has_a_partial_index_for_the_claim_query() -> None:
    # `... WHERE status='queued' AND available_at <= now() FOR UPDATE SKIP LOCKED`
    index = _index("ingestion_jobs", "ix_ingestion_jobs_claimable")
    assert [c.name for c in index.columns] == ["available_at"]
    assert "status = 'queued'" in _where(index)


def test_the_queue_tracks_its_lock() -> None:
    # locked_at is the visibility timeout: a lock older than the timeout means
    # the worker died and the job is claimable again.
    columns = TABLES["ingestion_jobs"].columns
    assert columns["locked_at"].nullable
    assert columns["locked_by"].nullable
    assert not columns["attempts"].nullable


# ── The approval gate ───────────────────────────────────────────────────────


def test_a_proposal_cannot_be_approved_twice() -> None:
    assert ("idempotency_key",) in _uniques("action_proposals")


def test_a_proposal_carries_the_hash_that_prevents_tampering() -> None:
    # Re-checked at approval, so the payload cannot be swapped between what a
    # human saw and what gets executed.
    assert not TABLES["action_proposals"].columns["args_hash"].nullable


def test_a_proposal_expires() -> None:
    assert not TABLES["action_proposals"].columns["expires_at"].nullable


def test_a_decided_proposal_must_record_when() -> None:
    """ "Approved by nobody at no time" must not be representable."""
    checks = [
        str(c.sqltext)
        for c in TABLES["action_proposals"].constraints
        if isinstance(c, CheckConstraint)
    ]
    assert any("decided_at IS NOT NULL" in c for c in checks)


def test_a_proposal_can_resume_its_paused_graph() -> None:
    assert "langgraph_thread_id" in TABLES["action_proposals"].columns


# ── Conversations and audit ─────────────────────────────────────────────────


def test_message_content_is_structured_not_flattened() -> None:
    """Replaying a conversation needs the tool-call blocks intact.

    Flattening to text is a one-way door: you cannot reconstruct which tool
    produced which passage once it is gone.
    """
    from sqlalchemy.dialects.postgresql import JSONB

    assert isinstance(TABLES["messages"].columns["content"].type, JSONB)


def test_cost_is_recorded_per_message() -> None:
    # Cost is a production metric, not a month-end surprise -- and the provider
    # benchmark needs real per-answer numbers.
    for column in ("input_tokens", "output_tokens", "cached_input_tokens", "cost_usd"):
        assert column in TABLES["messages"].columns


def test_audit_distinguishes_pii_reads() -> None:
    # "Who looked at student data, and when" must be one query, not an
    # inference over every row.
    assert not TABLES["audit_events"].columns["pii_accessed"].nullable
    assert "ix_audit_events_pii" in {ix.name for ix in TABLES["audit_events"].indexes}


# ── Tenancy coverage ────────────────────────────────────────────────────────


def test_every_table_carries_tenant_id() -> None:
    # Including the join tables. A row without a tenant cannot be scoped, and
    # the isolation tests would have nothing to assert against.
    for name in EXPECTED_TABLES:
        assert "tenant_id" in TABLES[name].columns, f"{name} is missing tenant_id"
