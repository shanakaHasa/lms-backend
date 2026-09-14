"""initial LMS schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-01 09:00:00

The first migration, so everything is additive by definition. Later migrations
follow the expand/contract rules in the template header.

Note on constraint names: the metadata naming convention is
`ck_%(table_name)s_%(constraint_name)s`, so CheckConstraints here pass the SHORT
name and let the convention prepend the prefix. Passing the full name produces
`ck_students_ck_students_status_valid` -- a real bug the drift checker caught in
the sibling service.

pgvector is deliberately NOT here. The `embedding` column and the extension land
in a separate migration at step B5a, so this one applies on any Postgres
regardless of whether the extension is available.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── students ────────────────────────────────────────────────────────────
    op.create_table(
        "students",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("student_number", sa.String(32), nullable=False),
        sa.Column("first_name", sa.String(128), nullable=False),
        sa.Column("last_name", sa.String(128), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("email_normalized", sa.String(320), nullable=False),
        sa.Column("year_level", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column(
            "student_metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('active','inactive','graduated','withdrawn')", name="status_valid"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_students"),
        # Enrolments carries a composite FK into (id, tenant_id); Postgres needs
        # a matching unique constraint to reference.
        sa.UniqueConstraint("id", "tenant_id", name="uq_students_id_tenant"),
    )
    # Unique per tenant, live rows only -- so an identifier is reusable after a
    # deletion.
    op.create_index(
        "uq_students_tenant_number",
        "students",
        ["tenant_id", "student_number"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_students_tenant_email",
        "students",
        ["tenant_id", "email_normalized"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_students_tenant_status", "students", ["tenant_id", "status"])
    op.create_index("ix_students_tenant_last_name", "students", ["tenant_id", "last_name"])

    # ── courses ─────────────────────────────────────────────────────────────
    op.create_table(
        "courses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("term", sa.String(32), nullable=True),
        sa.Column("teacher_user_id", sa.String(128), nullable=True),
        sa.Column("credits", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("status IN ('active','archived')", name="status_valid"),
        sa.PrimaryKeyConstraint("id", name="pk_courses"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_courses_tenant_code"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_courses_id_tenant"),
    )
    op.create_index("ix_courses_tenant_teacher", "courses", ["tenant_id", "teacher_user_id"])

    # ── enrolments ──────────────────────────────────────────────────────────
    op.create_table(
        "enrolments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("student_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="enrolled"),
        sa.Column("grade", sa.String(8), nullable=True),
        sa.Column(
            "enrolled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("status IN ('enrolled','completed','withdrawn')", name="status_valid"),
        # The assistant proposes enrolments, so a duplicated or retried proposal
        # must be structurally incapable of producing a second row.
        sa.UniqueConstraint("student_id", "course_id", name="uq_enrolments_student_course"),
        # Both sides validated against the SAME tenant, so joining a student in
        # one institution to a course in another is a database error.
        sa.ForeignKeyConstraint(
            ["student_id", "tenant_id"],
            ["students.id", "students.tenant_id"],
            name="fk_enrolments_student_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "tenant_id"],
            ["courses.id", "courses.tenant_id"],
            name="fk_enrolments_course_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_enrolments"),
    )
    op.create_index("ix_enrolments_course", "enrolments", ["course_id", "status"])

    # ── course_materials ────────────────────────────────────────────────────
    op.create_table(
        "course_materials",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        # NULL means institution-wide: a policy belongs to no single course.
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("doc_type", sa.String(32), nullable=False, server_default="syllabus"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("uploaded_by", sa.String(128), nullable=False),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "doc_type IN ('syllabus','lecture_notes','assignment_brief','policy','handbook')",
            name="doc_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('pending','processing','indexed','failed','deleted')", name="status_valid"
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name="fk_course_materials_course_id_courses",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_course_materials"),
        # Content-addressed: re-uploading the same file is a no-op rather than a
        # duplicate that doubles every retrieval result.
        sa.UniqueConstraint("tenant_id", "sha256", name="uq_course_materials_tenant_sha256"),
    )
    op.create_index(
        "ix_course_materials_tenant_status", "course_materials", ["tenant_id", "status"]
    )
    op.create_index("ix_course_materials_course", "course_materials", ["course_id"])

    # ── material_chunks ─────────────────────────────────────────────────────
    op.create_table(
        "material_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("material_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("page_from", sa.Integer(), nullable=True),
        sa.Column("page_to", sa.Integer(), nullable=True),
        sa.Column("section", sa.String(512), nullable=True),
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),
        # Generated, not trigger-maintained: Postgres keeps it in step with the
        # text itself, so keyword search cannot drift from what the model sees.
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', text)", persisted=True),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["material_id"],
            ["course_materials.id"],
            name="fk_material_chunks_material_id_course_materials",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_material_chunks"),
        sa.UniqueConstraint("material_id", "ordinal", name="uq_material_chunks_ordinal"),
    )
    op.create_index("ix_material_chunks_tsv", "material_chunks", ["tsv"], postgresql_using="gin")
    op.create_index(
        "ix_material_chunks_tenant_material", "material_chunks", ["tenant_id", "material_id"]
    )
    # Chunks written but never embedded -- the recoverable state left when
    # ingestion dies between Postgres and the vector index.
    op.create_index(
        "ix_material_chunks_unembedded",
        "material_chunks",
        ["tenant_id"],
        postgresql_where=sa.text("embedded_at IS NULL"),
    )

    # ── ingestion_jobs (this table is the queue) ────────────────────────────
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("material_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','dead_letter')", name="status_valid"
        ),
        sa.ForeignKeyConstraint(
            ["material_id"],
            ["course_materials.id"],
            name="fk_ingestion_jobs_material_id_course_materials",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_jobs"),
        # Delivery is at-least-once by design; this turns a redelivery into a
        # no-op instead of a duplicate set of chunks.
        sa.UniqueConstraint("idempotency_key", name="uq_ingestion_jobs_idempotency_key"),
    )
    # The claim query's index. Partial, because a healthy queue is mostly
    # finished rows and indexing those is wasted.
    op.create_index(
        "ix_ingestion_jobs_claimable",
        "ingestion_jobs",
        ["available_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_ingestion_jobs_locked",
        "ingestion_jobs",
        ["locked_at"],
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index("ix_ingestion_jobs_material", "ingestion_jobs", ["material_id"])

    # ── conversations ───────────────────────────────────────────────────────
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
    )
    op.create_index("ix_conversations_tenant_user", "conversations", ["tenant_id", "user_id"])

    # ── messages ────────────────────────────────────────────────────────────
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        # The FULL content-block list, not flattened text: replaying a
        # conversation needs the tool-call blocks intact.
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column(
            "citations", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("stop_reason", sa.String(32), nullable=True),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("langfuse_trace_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("role IN ('user','assistant','system')", name="role_valid"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_messages_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
    )
    op.create_index("ix_messages_conversation", "messages", ["conversation_id", "created_at"])
    op.create_index("ix_messages_trace", "messages", ["langfuse_trace_id"])

    # ── action_proposals ────────────────────────────────────────────────────
    op.create_table(
        "action_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("proposed_by_user_id", sa.String(128), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), nullable=False),
        sa.Column(
            "preview", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        # Re-checked at approval, so the payload cannot be swapped between what
        # a human saw and what gets executed.
        sa.Column("args_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by_user_id", sa.String(128), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("executed_entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("langgraph_thread_id", sa.String(128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','expired','failed')", name="status_valid"
        ),
        # "Approved by nobody at no time" must not be a representable state.
        sa.CheckConstraint(
            "status = 'pending' OR status = 'expired' OR (decided_at IS NOT NULL)",
            name="decided_has_timestamp",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_action_proposals_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_action_proposals"),
        # An agent retry reuses the proposal; approving twice writes one row.
        sa.UniqueConstraint("idempotency_key", name="uq_action_proposals_idempotency_key"),
    )
    op.create_index(
        "ix_action_proposals_pending",
        "action_proposals",
        ["tenant_id", "expires_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index("ix_action_proposals_conversation", "action_proposals", ["conversation_id"])

    # ── audit_events ────────────────────────────────────────────────────────
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(128), nullable=True),
        sa.Column("pii_accessed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("source_ip", sa.String(64), nullable=True),
        sa.Column(
            "event_metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
    )
    op.create_index("ix_audit_events_tenant_at", "audit_events", ["tenant_id", "at"])
    op.create_index("ix_audit_events_action_at", "audit_events", ["action", "at"])
    # The access-review query: who read student data, in what window.
    op.create_index(
        "ix_audit_events_pii",
        "audit_events",
        ["tenant_id", "at"],
        postgresql_where=sa.text("pii_accessed"),
    )


def downgrade() -> None:
    # Reverse dependency order.
    op.drop_table("audit_events")
    op.drop_table("action_proposals")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("ingestion_jobs")
    op.drop_table("material_chunks")
    op.drop_table("course_materials")
    op.drop_table("enrolments")
    op.drop_table("courses")
    op.drop_table("students")
