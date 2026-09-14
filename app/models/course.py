"""A course. `teacher_user_id` is an opaque id from the auth service."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk

__all__ = ["Course"]


class Course(Base, TimestampMixin):
    __tablename__ = "courses"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # Appears in questions verbatim ("what's in COMP201 week 3"), which is why
    # keyword retrieval matters alongside embeddings -- a vector search will
    # happily return COMP101 for it.
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    term: Mapped[str | None] = mapped_column(String(32))
    # No foreign key: this identifies a user in the auth service's database.
    teacher_user_id: Mapped[str | None] = mapped_column(String(128))
    credits: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_courses_tenant_code"),
        # Counterpart to the students constraint, for the composite FK on
        # enrolments.
        UniqueConstraint("id", "tenant_id", name="uq_courses_id_tenant"),
        Index("ix_courses_tenant_teacher", "tenant_id", "teacher_user_id"),
        CheckConstraint("status IN ('active','archived')", name="status_valid"),
    )
