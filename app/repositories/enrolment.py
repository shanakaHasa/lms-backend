"""Enrolment persistence.

The roster query joins to `students` and re-applies `students.deleted_at IS
NULL`. That looks redundant next to the `ON DELETE CASCADE` composite foreign
key, and is not: students are **soft** deleted, so the cascade never fires and
the enrolment row survives its student. Without the join predicate a deleted
student would keep appearing on rosters — which is precisely the kind of
"deleted but still visible" bug that makes a deletion request unanswerable.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy import ColumnElement, Select, func, select

from app.models.enrolment import Enrolment
from app.models.student import Student
from app.repositories.base import TenantScopedRepository

__all__ = [
    "EnrolmentRepository",
    "SqlEnrolmentRepository",
    "stmt_count_roster",
    "stmt_enrolment_for_pair",
    "stmt_get_enrolment",
    "stmt_list_for_student",
    "stmt_roster",
]


# ── Query construction ──────────────────────────────────────────────────────


def stmt_get_enrolment(tenant_id: str, enrolment_id: uuid.UUID) -> Select[tuple[Enrolment]]:
    return select(Enrolment).where(Enrolment.tenant_id == tenant_id, Enrolment.id == enrolment_id)


def stmt_enrolment_for_pair(
    tenant_id: str, student_id: uuid.UUID, course_id: uuid.UUID
) -> Select[tuple[Enrolment]]:
    """The lookup that makes enrolling idempotent.

    `UNIQUE (student_id, course_id)` guarantees at most one row, so this is the
    check the service runs before proposing to insert — and the reason a retried
    proposal from the assistant returns the existing enrolment rather than a
    constraint violation.
    """
    return select(Enrolment).where(
        Enrolment.tenant_id == tenant_id,
        Enrolment.student_id == student_id,
        Enrolment.course_id == course_id,
    )


def _roster_clauses(
    tenant_id: str, course_id: uuid.UUID, status: str | None
) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = [
        Enrolment.tenant_id == tenant_id,
        Enrolment.course_id == course_id,
        # See the module docstring: soft deletion means the cascade never fires.
        Student.deleted_at.is_(None),
    ]
    if status:
        clauses.append(Enrolment.status == status)
    return clauses


def stmt_roster(
    tenant_id: str,
    course_id: uuid.UUID,
    *,
    limit: int,
    offset: int,
    status: str | None = None,
) -> Select[tuple[Enrolment, Student]]:
    return (
        select(Enrolment, Student)
        .join(Student, Student.id == Enrolment.student_id)
        .where(*_roster_clauses(tenant_id, course_id, status))
        .order_by(Student.last_name, Student.first_name, Student.id)
        .limit(limit)
        .offset(offset)
    )


def stmt_count_roster(
    tenant_id: str, course_id: uuid.UUID, *, status: str | None = None
) -> Select[tuple[int]]:
    return (
        select(func.count(Enrolment.id))
        .join(Student, Student.id == Enrolment.student_id)
        .where(*_roster_clauses(tenant_id, course_id, status))
    )


def stmt_list_for_student(tenant_id: str, student_id: uuid.UUID) -> Select[tuple[Enrolment]]:
    return (
        select(Enrolment)
        .where(Enrolment.tenant_id == tenant_id, Enrolment.student_id == student_id)
        .order_by(Enrolment.enrolled_at.desc())
    )


# ── Port ────────────────────────────────────────────────────────────────────


class EnrolmentRepository(Protocol):
    tenant_id: str

    async def get(self, enrolment_id: uuid.UUID) -> Enrolment | None: ...

    async def for_pair(self, student_id: uuid.UUID, course_id: uuid.UUID) -> Enrolment | None: ...

    async def roster(
        self,
        course_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
    ) -> tuple[list[tuple[Enrolment, Student]], int]: ...

    async def for_student(self, student_id: uuid.UUID) -> list[Enrolment]: ...

    async def add(self, enrolment: Enrolment) -> Enrolment: ...

    async def touch(self, enrolment: Enrolment) -> Enrolment: ...


# ── Adapter ─────────────────────────────────────────────────────────────────


class SqlEnrolmentRepository(TenantScopedRepository):
    async def get(self, enrolment_id: uuid.UUID) -> Enrolment | None:
        result = await self.session.execute(stmt_get_enrolment(self.tenant_id, enrolment_id))
        return result.scalar_one_or_none()

    async def for_pair(self, student_id: uuid.UUID, course_id: uuid.UUID) -> Enrolment | None:
        result = await self.session.execute(
            stmt_enrolment_for_pair(self.tenant_id, student_id, course_id)
        )
        return result.scalar_one_or_none()

    async def roster(
        self,
        course_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
    ) -> tuple[list[tuple[Enrolment, Student]], int]:
        rows = await self.session.execute(
            stmt_roster(self.tenant_id, course_id, limit=limit, offset=offset, status=status)
        )
        total = await self.session.execute(
            stmt_count_roster(self.tenant_id, course_id, status=status)
        )
        return [(enrolment, student) for enrolment, student in rows.all()], int(total.scalar_one())

    async def for_student(self, student_id: uuid.UUID) -> list[Enrolment]:
        result = await self.session.execute(stmt_list_for_student(self.tenant_id, student_id))
        return list(result.scalars())

    async def add(self, enrolment: Enrolment) -> Enrolment:
        return await self.flush(enrolment)

    async def touch(self, enrolment: Enrolment) -> Enrolment:
        return await self.flush(enrolment)
