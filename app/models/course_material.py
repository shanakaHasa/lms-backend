"""An uploaded document: a syllabus, assignment brief, or institutional policy."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk

__all__ = ["CourseMaterial"]


class CourseMaterial(Base, TimestampMixin):
    __tablename__ = "course_materials"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # NULL means institution-wide -- an academic integrity policy or a student
    # handbook belongs to no single course, and the assistant must still find it.
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE")
    )

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    # Opaque to the application: a filesystem path locally, a blob name later.
    # The Storage protocol owns what it means.
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Content-addressed, so re-uploading the same file is a no-op rather than a
    # duplicate that quietly doubles every retrieval result.
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)

    doc_type: Mapped[str] = mapped_column(String(32), nullable=False, default="syllabus")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[str] = mapped_column(String(128), nullable=False)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("tenant_id", "sha256", name="uq_course_materials_tenant_sha256"),
        Index("ix_course_materials_tenant_status", "tenant_id", "status"),
        Index("ix_course_materials_course", "course_id"),
        CheckConstraint(
            "doc_type IN ('syllabus','lecture_notes','assignment_brief','policy','handbook')",
            name="doc_type_valid",
        ),
        CheckConstraint(
            "status IN ('pending','processing','indexed','failed','deleted')",
            name="status_valid",
        ),
    )
