"""Pinecone serverless index, behind a protocol.

Two design choices carried over deliberately:

  * **One index, one namespace per tenant.** A namespace is Pinecone's hard
    partition -- a query cannot cross one -- which makes cross-tenant leakage a
    configuration error rather than a missing filter.
  * **The vector payload carries ids and non-PII metadata only.** Chunk text
    lives in Postgres. That keeps student material inside our own database, and
    means the index can be deleted and rebuilt from Postgres at any time.

The protocol exists so pgvector remains a swap rather than a rewrite. It is not
speculative generality: the two stores have genuinely different operational
characteristics, and the eval suite is where that comparison would be settled.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

from app.core.config import settings
from app.core.errors import UpstreamUnavailable
from app.core.logging import get_logger

log = get_logger(__name__)

_UPSERT_BATCH = 100


@dataclass(frozen=True)
class VectorMatch:
    chunk_id: str
    score: float
    metadata: dict[str, Any]


class VectorStore(Protocol):
    async def upsert(
        self, tenant_id: str, records: list[tuple[str, list[float], dict[str, Any]]]
    ) -> int: ...

    async def query(
        self,
        tenant_id: str,
        vector: list[float],
        top_k: int,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[VectorMatch]: ...

    async def delete(self, tenant_id: str, chunk_ids: list[str]) -> None: ...


def namespace_for(tenant_id: str) -> str:
    return f"tenant__{tenant_id}"


@lru_cache
def _index() -> Any:
    if not settings.pinecone_api_key:
        raise UpstreamUnavailable("PINECONE_API_KEY is not set")
    from pinecone import Pinecone, ServerlessSpec

    pc = Pinecone(api_key=settings.pinecone_api_key)
    existing = {i["name"] for i in pc.list_indexes()}
    if settings.pinecone_index not in existing:
        log.info("pinecone_creating_index", index=settings.pinecone_index)
        pc.create_index(
            name=settings.pinecone_index,
            dimension=settings.embedding_dim,
            metric="cosine",
            spec=ServerlessSpec(cloud=settings.pinecone_cloud, region=settings.pinecone_region),
        )
    return pc.Index(settings.pinecone_index)


class PineconeStore:
    """The default. Index dimension is fixed at creation, which is why the
    index name carries the embedding model and dimension -- changing provider
    means a new index and a full reindex, not a config change."""

    def _upsert_sync(self, vectors: list[dict[str, Any]], namespace: str) -> None:
        index = _index()
        for start in range(0, len(vectors), _UPSERT_BATCH):
            index.upsert(vectors=vectors[start : start + _UPSERT_BATCH], namespace=namespace)

    async def upsert(
        self, tenant_id: str, records: list[tuple[str, list[float], dict[str, Any]]]
    ) -> int:
        """records: (chunk_id, embedding, metadata). Metadata must be PII-free."""
        if not records:
            return 0
        vectors = [{"id": cid, "values": vec, "metadata": meta} for cid, vec, meta in records]
        try:
            await asyncio.to_thread(self._upsert_sync, vectors, namespace_for(tenant_id))
        except Exception as exc:
            log.error("pinecone_upsert_failed", tenant_id=tenant_id, error=str(exc))
            raise UpstreamUnavailable(f"vector upsert failed: {exc}") from exc
        return len(vectors)

    def _query_sync(
        self, vector: list[float], namespace: str, top_k: int, flt: dict[str, Any] | None
    ) -> Any:
        return _index().query(
            vector=vector,
            top_k=top_k,
            namespace=namespace,
            include_metadata=True,
            filter=flt or None,
        )

    async def query(
        self,
        tenant_id: str,
        vector: list[float],
        top_k: int,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[VectorMatch]:
        try:
            result = await asyncio.to_thread(
                self._query_sync, vector, namespace_for(tenant_id), top_k, metadata_filter
            )
        except Exception as exc:
            log.error("pinecone_query_failed", tenant_id=tenant_id, error=str(exc))
            raise UpstreamUnavailable(f"vector query failed: {exc}") from exc

        return [
            VectorMatch(chunk_id=m["id"], score=float(m["score"]), metadata=m.get("metadata") or {})
            for m in result.get("matches", [])
        ]

    def _delete_sync(self, chunk_ids: list[str], namespace: str) -> None:
        _index().delete(ids=chunk_ids, namespace=namespace)

    async def delete(self, tenant_id: str, chunk_ids: list[str]) -> None:
        """Called when a material is deleted.

        Vectors go before the row is soft-deleted: a vector whose row is gone
        produces a citation to a document nobody can open, whereas a row whose
        vector is gone is merely invisible to dense search.
        """
        if not chunk_ids:
            return
        await asyncio.to_thread(self._delete_sync, chunk_ids, namespace_for(tenant_id))


def get_vector_store() -> VectorStore:
    return PineconeStore()
