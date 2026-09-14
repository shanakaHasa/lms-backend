"""Student persistence.

Every statement below carries two predicates that are never optional:
`tenant_id = :tenant` and `deleted_at IS NULL`. The second is what makes soft
deletion mean anything — a deleted student that still appears in a list or a
roster has not been deleted, it has merely been marked.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy import ColumnElement, Select, func, or_, select

from app.models.student import Student
from app.repositories.base import TenantScopedRepository

__all__ = [
    "SqlStudentRepository",
    "StudentRepository",
    "stmt_count_students",
    "stmt_get_student",
    "stmt_list_students",
    "stmt_student_by_email",
    "stmt_student_by_number",
    "stmt_students_by_ids",
]


# ── Query construction (pure, session-free, compiled in tests) ───────────────


def _live(tenant_id: str) -> list[ColumnElement[bool]]:
    """The two predicates every student query starts from."""
    return [Student.tenant_id == tenant_id, Student.deleted_at.is_(None)]


def stmt_get_student(tenant_id: str, student_id: uuid.UUID) -> Select[tuple[Student]]:
    return select(Student).where(*_live(tenant_id), Student.id == student_id)


def stmt_students_by_ids(tenant_id: str, student_ids: list[uuid.UUID]) -> Select[tuple[Student]]:
    """Resolve many students at once.

    The roster needs this: fetching students one per enrolment row is the N+1
    that turns a 40-student roster into 41 round trips.
    """
    return select(Student).where(*_live(tenant_id), Student.id.in_(student_ids))


def stmt_student_by_number(tenant_id: str, student_number: str) -> Select[tuple[Student]]:
    return select(Student).where(*_live(tenant_id), Student.student_number == student_number)


def stmt_student_by_email(tenant_id: str, email_normalized: str) -> Select[tuple[Student]]:
    return select(Student).where(*_live(tenant_id), Student.email_normalized == email_normalized)


def _filtered(tenant_id: str, status: str | None, query: str | None) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = _live(tenant_id)
    if status:
        clauses.append(Student.status == status)
    if query:
        # ILIKE, not full-text: this is a "find the student I am thinking of"
        # box over a few thousand rows, and `ix_students_tenant_last_name`
        # already narrows by tenant. Full-text search is reserved for material
        # chunks, where it is fused with embeddings and actually earns its cost.
        pattern = f"%{query.strip()}%"
        clauses.append(
            or_(
                Student.first_name.ilike(pattern),
                Student.last_name.ilike(pattern),
                Student.student_number.ilike(pattern),
                Student.email_normalized.ilike(pattern),
            )
        )
    return clauses


def stmt_list_students(
    tenant_id: str,
    *,
    limit: int,
    offset: int,
    status: str | None = None,
    query: str | None = None,
) -> Select[tuple[Student]]:
    return (
        select(Student)
        .where(*_filtered(tenant_id, status, query))
        # Deterministic, and stable across pages: without a total order, two
        # requests for the same offset can legitimately return different rows.
        .order_by(Student.last_name, Student.first_name, Student.id)
        .limit(limit)
        .offset(offset)
    )


def stmt_count_students(
    tenant_id: str, *, status: str | None = None, query: str | None = None
) -> Select[tuple[int]]:
    return select(func.count(Student.id)).where(*_filtered(tenant_id, status, query))


# ── The port the services depend on ─────────────────────────────────────────


class StudentRepository(Protocol):
    """What a service needs from student storage.

    Deliberately narrow, and deliberately free of a tenant argument: the tenant
    is fixed when the repository is built, from the verified token.
    """

    tenant_id: str

    async def get(self, student_id: uuid.UUID) -> Student | None: ...

    async def get_many(self, student_ids: list[uuid.UUID]) -> dict[uuid.UUID, Student]: ...

    async def by_number(self, student_number: str) -> Student | None: ...

    async def by_email(self, email_normalized: str) -> Student | None: ...

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
        query: str | None = None,
    ) -> tuple[list[Student], int]: ...

    async def add(self, student: Student) -> Student: ...

    async def touch(self, student: Student) -> Student: ...


# ── The adapter ─────────────────────────────────────────────────────────────


class SqlStudentRepository(TenantScopedRepository):
    """Executes the statements above. Contains no business rules."""

    async def get(self, student_id: uuid.UUID) -> Student | None:
        result = await self.session.execute(stmt_get_student(self.tenant_id, student_id))
        return result.scalar_one_or_none()

    async def get_many(self, student_ids: list[uuid.UUID]) -> dict[uuid.UUID, Student]:
        if not student_ids:
            return {}
        result = await self.session.execute(stmt_students_by_ids(self.tenant_id, student_ids))
        return {student.id: student for student in result.scalars()}

    async def by_number(self, student_number: str) -> Student | None:
        result = await self.session.execute(stmt_student_by_number(self.tenant_id, student_number))
        return result.scalar_one_or_none()

    async def by_email(self, email_normalized: str) -> Student | None:
        result = await self.session.execute(stmt_student_by_email(self.tenant_id, email_normalized))
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
        query: str | None = None,
    ) -> tuple[list[Student], int]:
        rows = await self.session.execute(
            stmt_list_students(
                self.tenant_id, limit=limit, offset=offset, status=status, query=query
            )
        )
        total = await self.session.execute(
            stmt_count_students(self.tenant_id, status=status, query=query)
        )
        return list(rows.scalars()), int(total.scalar_one())

    async def add(self, student: Student) -> Student:
        return await self.flush(student)

    async def touch(self, student: Student) -> Student:
        """Flush an already-loaded, already-mutated instance.

        Updates and soft deletes both come through here, because the unique
        indexes can be violated by a *change* of email just as easily as by an
        insert.
        """
        return await self.flush(student)
