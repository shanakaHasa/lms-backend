"""A retrievable passage.

This table is the system of record for retrieval. Vectors live in whichever
store is configured -- pgvector, Pinecone or Azure AI Search -- and carry only
ids and non-PII metadata. The text and its provenance stay here, which means any
index can be dropped and rebuilt from Postgres at any time.

The `tsv` column is **generated**, not trigger-maintained. Postgres keeps it in
step with `text` itself, so keyword search can never drift from the passage the
model actually sees. A trigger can be dropped, disabled, or skipped by a bulk
load; a generated column cannot.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

# Aliased: this model has a column called `text`, which shadows the bare
# `text()` function inside the class body and turns a partial-index predicate
# into a call on a MappedColumn.
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk

__all__ = ["MaterialChunk"]


class MaterialChunk(Base, TimestampMixin):
    __tablename__ = "material_chunks"

    id: Mapped[uuid.UUID] = uuid_pk()
    material_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course_materials.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)

    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # Carried all the way to the citation, so a teacher can open the source PDF
    # at the right page.
    page_from: Mapped[int | None] = mapped_column(Integer)
    page_to: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(512))
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', text)", persisted=True),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("material_id", "ordinal", name="uq_material_chunks_ordinal"),
        Index("ix_material_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_material_chunks_tenant_material", "tenant_id", "material_id"),
        # Finds chunks written but never embedded -- the recoverable state left
        # behind when ingestion dies between Postgres and the vector index.
        # Partial, because in a healthy system this set is empty and a full
        # index over every chunk would be wasted.
        Index(
            "ix_material_chunks_unembedded",
            "tenant_id",
            postgresql_where=sql_text("embedded_at IS NULL"),
        ),
    )
