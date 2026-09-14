"""Course persistence.

Courses are not soft-deleted — they are archived, which is a status rather than
a disappearance. Materials, enrolments and grades all point at a course, and a
course that vanishes takes the meaning of those rows with it.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy import ColumnElement, Select, func, or_, select

from app.models.course import Course
from app.repositories.base import TenantScopedRepository

__all__ = [
    "CourseRepository",
    "SqlCourseRepository",
    "stmt_count_courses",
    "stmt_course_by_code",
    "stmt_get_course",
    "stmt_list_courses",
]


# ── Query construction ──────────────────────────────────────────────────────


def stmt_get_course(tenant_id: str, course_id: uuid.UUID) -> Select[tuple[Course]]:
    return select(Course).where(Course.tenant_id == tenant_id, Course.id == course_id)


def stmt_course_by_code(tenant_id: str, code: str) -> Select[tuple[Course]]:
    return select(Course).where(Course.tenant_id == tenant_id, Course.code == code)


def _filtered(
    tenant_id: str, status: str | None, query: str | None, teacher_user_id: str | None
) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = [Course.tenant_id == tenant_id]
    if status:
        clauses.append(Course.status == status)
    if teacher_user_id:
        clauses.append(Course.teacher_user_id == teacher_user_id)
    if query:
        pattern = f"%{query.strip()}%"
        clauses.append(or_(Course.code.ilike(pattern), Course.title.ilike(pattern)))
    return clauses


def stmt_list_courses(
    tenant_id: str,
    *,
    limit: int,
    offset: int,
    status: str | None = None,
    query: str | None = None,
    teacher_user_id: str | None = None,
) -> Select[tuple[Course]]:
    return (
        select(Course)
        .where(*_filtered(tenant_id, status, query, teacher_user_id))
        .order_by(Course.code, Course.id)
        .limit(limit)
        .offset(offset)
    )


def stmt_count_courses(
    tenant_id: str,
    *,
    status: str | None = None,
    query: str | None = None,
    teacher_user_id: str | None = None,
) -> Select[tuple[int]]:
    return select(func.count(Course.id)).where(
        *_filtered(tenant_id, status, query, teacher_user_id)
    )


# ── Port ────────────────────────────────────────────────────────────────────


class CourseRepository(Protocol):
    tenant_id: str

    async def get(self, course_id: uuid.UUID) -> Course | None: ...

    async def by_code(self, code: str) -> Course | None: ...

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
        query: str | None = None,
        teacher_user_id: str | None = None,
    ) -> tuple[list[Course], int]: ...

    async def add(self, course: Course) -> Course: ...

    async def touch(self, course: Course) -> Course: ...


# ── Adapter ─────────────────────────────────────────────────────────────────


class SqlCourseRepository(TenantScopedRepository):
    async def get(self, course_id: uuid.UUID) -> Course | None:
        result = await self.session.execute(stmt_get_course(self.tenant_id, course_id))
        return result.scalar_one_or_none()

    async def by_code(self, code: str) -> Course | None:
        result = await self.session.execute(stmt_course_by_code(self.tenant_id, code))
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
        query: str | None = None,
        teacher_user_id: str | None = None,
    ) -> tuple[list[Course], int]:
        rows = await self.session.execute(
            stmt_list_courses(
                self.tenant_id,
                limit=limit,
                offset=offset,
                status=status,
                query=query,
                teacher_user_id=teacher_user_id,
            )
        )
        total = await self.session.execute(
            stmt_count_courses(
                self.tenant_id, status=status, query=query, teacher_user_id=teacher_user_id
            )
        )
        return list(rows.scalars()), int(total.scalar_one())

    async def add(self, course: Course) -> Course:
        return await self.flush(course)

    async def touch(self, course: Course) -> Course:
        return await self.flush(course)
