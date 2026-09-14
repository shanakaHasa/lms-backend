"""The audit trail.

Written through the **same session** as the action it records, so the two
commit together. An audit row in a separate transaction is worse than none: it
can succeed while the action rolls back, or fail while the action commits, and
in both cases the log now lies about what happened.

What gets recorded, decided deliberately:

* **Every write.** Creating, changing, deleting, enrolling, withdrawing.
* **Every single-record read of a person.** `GET /students/{id}` writes a row
  with `pii_accessed = true`, because fetching one named student is the access
  an investigation actually asks about.
* **One row per list**, carrying the result count — not one row per student
  returned. Audit volume should track intent, not page size; a 50-row page is
  one act of looking, and fifty rows would bury the single lookup that matters.

`pii_accessed` is what makes the partial index earn its place: "who read student
data, in this window" becomes one indexed query rather than an inference over
every row in the table.
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import request_id_ctx
from app.core.security import Principal
from app.models.audit_event import AuditEvent

__all__ = ["AuditSink", "SqlAuditSink"]


class AuditSink(Protocol):
    """What a service needs in order to leave a trail."""

    def record(
        self,
        action: str,
        resource_type: str,
        *,
        resource_id: str | None = None,
        pii_accessed: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...


class SqlAuditSink:
    """Appends to `audit_events` in the caller's transaction.

    Synchronous and non-awaiting on purpose: it only stages an INSERT on the
    session. Making it `async` would suggest it does I/O of its own and invite
    someone to commit it separately, which is the exact failure this design
    exists to prevent.
    """

    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.session = session
        self.principal = principal

    def record(
        self,
        action: str,
        resource_type: str,
        *,
        resource_id: str | None = None,
        pii_accessed: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.session.add(
            AuditEvent(
                tenant_id=self.principal.tenant_id,
                actor_id=self.principal.user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                pii_accessed=pii_accessed,
                # Ties the row to the request's log lines and, once tracing is
                # on, to the trace — so "what else did this actor do in that
                # request" is answerable.
                request_id=request_id_ctx.get(),
                event_metadata=metadata or {},
            )
        )
