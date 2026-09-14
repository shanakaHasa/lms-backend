"""A database change the assistant proposed and a human has not yet approved.

This table is the reason the agent can be trusted near a system of record. A
`propose_*` tool writes **only here**; there is no code path where a
model-generated argument reaches `students` or `enrolments` without someone
with `proposals:approve` deciding.

Four columns do the security work:

  * `args_hash` — re-checked at approval, so the payload cannot be swapped
    between what a human saw and what gets executed.
  * `idempotency_key` — UNIQUE, derived from the tool-call id. An agent retry
    reuses the proposal; approving twice writes one row.
  * `expires_at` — a stale proposal cannot be approved after the world moved on.
  * `approved_by_user_id` — execution runs under the approver's identity and
    scopes, re-checked at approval time, so the agent cannot escalate past the
    person approving it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk

__all__ = ["ActionProposal"]


class ActionProposal(Base, TimestampMixin):
    __tablename__ = "action_proposals"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    proposed_by_user_id: Mapped[str] = mapped_column(String(128), nullable=False)

    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Human-readable summary plus a field-level diff, computed at proposal time.
    # This is what the approval card renders -- and what the approver is
    # actually consenting to.
    preview: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # SHA-256 over the canonical arguments.
    args_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    approved_by_user_id: Mapped[str | None] = mapped_column(String(128))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(Text)

    executed_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    error: Mapped[str | None] = mapped_column(Text)
    # Addresses the paused LangGraph run, so approval can resume a graph long
    # after the request that created it has ended.
    langgraph_thread_id: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        # Drives the "what is waiting for me" query.
        Index(
            "ix_action_proposals_pending",
            "tenant_id",
            "expires_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_action_proposals_conversation", "conversation_id"),
        CheckConstraint(
            "status IN ('pending','approved','rejected','expired','failed')",
            name="status_valid",
        ),
        # A decided proposal must record who decided it and when. Without this,
        # "approved by nobody at no time" is a representable state.
        CheckConstraint(
            "status = 'pending' OR status = 'expired' OR (decided_at IS NOT NULL)",
            name="decided_has_timestamp",
        ),
    )
