"""The tenant boundary, made structural.

A repository is constructed **with** a tenant and cannot be constructed without
one. Every query it builds starts from `self.tenant_id`, and no method accepts a
tenant argument — so "forgot the WHERE clause" is not a mistake this layer can
express, rather than one code review has to keep catching.

Two conventions hold throughout:

* **Query construction is a pure function**, separate from execution. Statements
  are built by module-level `stmt_*` functions that need no session, which is
  what lets the SQL be compiled against the Postgres dialect and asserted on
  with no database running. Given Phase 1 ships every table before a single
  migration is applied, that is the only thing standing between a typo'd column
  and a runtime failure in Phase 2.
* **A row belonging to another tenant is indistinguishable from a row that does
  not exist.** Queries return `None`, and services raise `NotFound`. Returning
  403 would confirm the row exists, which is an enumeration oracle across
  institutions.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict

__all__ = ["TenantScopedRepository", "translate_integrity_error"]


class TenantScopedRepository:
    """Base for every repository in this service."""

    def __init__(self, session: AsyncSession, tenant_id: str) -> None:
        if not tenant_id:
            # A blank tenant would scope every query to nothing at best, and to
            # everything at worst if a later refactor treated it as "unset".
            raise ValueError("tenant_id is required to build a repository")
        self.session = session
        self.tenant_id = tenant_id

    async def flush[T](self, instance: T) -> T:
        """Persist far enough to surface constraint violations here.

        Without the flush, a duplicate would not raise until the session commits
        in `get_session` — after the route has returned 201 and after the audit
        row was written. Flushing keeps the failure inside the handler where it
        can become a 409.
        """
        self.session.add(instance)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise translate_integrity_error(exc) from exc
        return instance


# Constraint name → the message a caller can act on. The database is the
# backstop: services check first so the common case gets a precise error, and
# this catches the race where two requests check simultaneously and both pass.
_CONSTRAINT_MESSAGES = {
    "uq_students_tenant_number": "a student with that number already exists",
    "uq_students_tenant_email": "a student with that email already exists",
    "uq_courses_tenant_code": "a course with that code already exists",
    "uq_enrolments_student_course": "that student is already enrolled in that course",
    "uq_course_materials_tenant_sha256": "that file has already been uploaded",
}


def translate_integrity_error(exc: IntegrityError) -> Conflict:
    """Turn a Postgres constraint violation into a 409 a client can act on.

    The alternative is a 500, which tells the caller nothing and pages someone
    for what is a perfectly ordinary duplicate submission.
    """
    detail = str(getattr(exc, "orig", exc))
    for constraint, message in _CONSTRAINT_MESSAGES.items():
        if constraint in detail:
            return Conflict(message, detail={"constraint": constraint})
    return Conflict("that change conflicts with an existing record")
