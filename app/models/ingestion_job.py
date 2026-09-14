"""An ingestion job. This table **is** the queue.

`SELECT ... FOR UPDATE SKIP LOCKED` gives the same semantics SQS does --
at-least-once delivery, concurrent consumers without double-processing, and a
visibility timeout via `locked_at` -- without a second service to run or pay
for. The worker code is written against a `JobQueue` protocol, so swapping in
Azure Storage Queues later changes one adapter and nothing else.

The claim query this is shaped for:

    SELECT * FROM ingestion_jobs
     WHERE status = 'queued' AND available_at <= now()
     ORDER BY available_at
     FOR UPDATE SKIP LOCKED
     LIMIT :n
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk

__all__ = ["IngestionJob"]


class IngestionJob(Base, TimestampMixin):
    __tablename__ = "ingestion_jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    material_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course_materials.id", ondelete="CASCADE"), nullable=False
    )

    # Delivery is at-least-once by design. The unique constraint is what turns a
    # redelivery into a no-op instead of a duplicate set of chunks.
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Not visible to a consumer until this time -- drives both the initial
    # enqueue and the exponential backoff after a failure.
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # The visibility timeout: a job whose lock is older than the timeout is
    # considered abandoned and becomes claimable again.
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(64))

    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # The claim query's index. Partial, because a healthy queue is mostly
        # empty of queued rows and indexing the finished ones is wasted.
        Index(
            "ix_ingestion_jobs_claimable",
            "available_at",
            postgresql_where=text("status = 'queued'"),
        ),
        # Finds jobs whose worker died holding the lock.
        Index(
            "ix_ingestion_jobs_locked",
            "locked_at",
            postgresql_where=text("status = 'running'"),
        ),
        Index("ix_ingestion_jobs_material", "material_id"),
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','dead_letter')",
            name="status_valid",
        ),
    )
