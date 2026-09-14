"""A student's place in a course.

Two constraints carry real weight here, and both matter more than usual because
**the assistant proposes enrolments**. A model-generated argument that slipped
through would land in this table.

  * `UNIQUE (student_id, course_id)` makes double-enrolment impossible rather
    than merely unlikely — so a retried or duplicated proposal cannot produce a
    second row.
  * The composite foreign keys validate student and course against the *same*
    tenant, so an enrolment joining a student in one institution to a course in
    another is a database error, not something a missing WHERE clause can cause.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk

__all__ = ["Enrolment"]


class Enrolment(Base, TimestampMixin):
    __tablename__ = "enrolments"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    course_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="enrolled", server_default="enrolled"
    )
    grade: Mapped[str | None] = mapped_column(String(8))
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # The constraint that makes a duplicated proposal harmless.
        UniqueConstraint("student_id", "course_id", name="uq_enrolments_student_course"),
        ForeignKeyConstraint(
            ["student_id", "tenant_id"],
            ["students.id", "students.tenant_id"],
            name="fk_enrolments_student_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["course_id", "tenant_id"],
            ["courses.id", "courses.tenant_id"],
            name="fk_enrolments_course_tenant",
            ondelete="CASCADE",
        ),
        # Drives the course roster.
        Index("ix_enrolments_course", "course_id", "status"),
        CheckConstraint("status IN ('enrolled','completed','withdrawn')", name="status_valid"),
    )
