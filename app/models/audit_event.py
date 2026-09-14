"""Append-only audit log.

Written in the same transaction as the action it records, so an action cannot
commit without its audit row.

`pii_accessed` is the column that matters for an access review: it distinguishes
reading a course list from reading a student record, so "who looked at student
data, and when" is one query rather than an inference over every row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

__all__ = ["AuditEvent"]


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)

    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(128))

    pii_accessed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    request_id: Mapped[str | None] = mapped_column(String(64))
    source_ip: Mapped[str | None] = mapped_column(String(64))
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        Index("ix_audit_events_tenant_at", "tenant_id", "at"),
        Index("ix_audit_events_action_at", "action", "at"),
        # The access-review query: who read student data, in what window.
        Index(
            "ix_audit_events_pii",
            "tenant_id",
            "at",
            postgresql_where=text("pii_accessed"),
        ),
    )
