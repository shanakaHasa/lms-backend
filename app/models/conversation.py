"""A chat thread. Its id is also the LangGraph thread id."""

from __future__ import annotations

import uuid

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk

__all__ = ["Conversation"]


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str | None] = mapped_column(String(512))

    __table_args__ = (Index("ix_conversations_tenant_user", "tenant_id", "user_id"),)
