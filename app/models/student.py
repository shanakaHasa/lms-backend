"""A student record. Personal information — treat every read as auditable."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk

__all__ = ["Student"]


class Student(Base, TimestampMixin):
    __tablename__ = "students"

    id: Mapped[uuid.UUID] = uuid_pk()
    # Opaque string from the verified token. There is deliberately no foreign
    # key into the auth service's database -- that is what makes the service
    # boundary real rather than a convention.
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)

    student_number: Mapped[str] = mapped_column(String(32), nullable=False)
    first_name: Mapped[str] = mapped_column(String(128), nullable=False)
    last_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # `email` keeps the form that was typed; `email_normalized` is what the
    # unique index and every lookup use.
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    year_level: Mapped[int | None] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    student_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Exists so enrolments can carry a composite foreign key into
        # (id, tenant_id); Postgres requires a matching unique constraint.
        UniqueConstraint("id", "tenant_id", name="uq_students_id_tenant"),
        # Both identifiers are unique PER TENANT and only among live rows, so a
        # student number or address becomes reusable after a deletion.
        Index(
            "uq_students_tenant_number",
            "tenant_id",
            "student_number",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_students_tenant_email",
            "tenant_id",
            "email_normalized",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_students_tenant_status", "tenant_id", "status"),
        # Drives the name search behind find_students.
        Index("ix_students_tenant_last_name", "tenant_id", "last_name"),
        CheckConstraint(
            "status IN ('active','inactive','graduated','withdrawn')", name="status_valid"
        ),
    )
