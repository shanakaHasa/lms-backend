"""One turn in a conversation.

`content` stores the **full content-block list**, not flattened text. Replaying
a conversation needs the tool-call and tool-result blocks intact, and flattening
them to a string is a one-way door -- you cannot reconstruct which tool produced
which passage once it is gone.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, uuid_pk

__all__ = ["Message"]


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)

    content: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    # Only the passages the answer actually cited, not everything retrieved --
    # a citation list that looks thorough while the answer cited nothing is the
    # failure the eval suite measures.
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    stop_reason: Mapped[str | None] = mapped_column(String(32))
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64))
    # Recorded per message so cost is a production metric rather than a
    # month-end surprise, and so the provider benchmark has real numbers.
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_messages_conversation", "conversation_id", "created_at"),
        Index("ix_messages_trace", "langfuse_trace_id"),
        CheckConstraint("role IN ('user','assistant','system')", name="role_valid"),
    )
