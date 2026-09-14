"""In-memory stand-ins for storage.

These exist because Phase 1 builds every feature — CRUD, ingestion, retrieval,
the agent, the approval gate — before a single migration is applied. Without
them the business rules would go unverified until the very end, which is exactly
the batch risk the plan set out to reduce.

Two properties make them worth trusting:

**The store is shared across tenants.** Every fake repository reads the same
dict, filtering by the tenant it was constructed with. A tenant filter that went
missing would therefore return the *other* tenant's rows, and the isolation
matrix would fail — which would not happen if each tenant had its own store.

**`add` and `touch` do what a flush does.** They stamp `created_at`,
`updated_at` and enforce the unique constraints the real schema declares, so a
service that relies on the database to populate or reject something fails here
too rather than passing under a laxer fake.

What they deliberately do *not* model is SQL. That is covered separately, by
compiling each real statement against the Postgres dialect.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.errors import Conflict
from app.models.course import Course
from app.models.enrolment import Enrolment
from app.models.student import Student

__all__ = [
    "FakeAuditSink",
    "FakeCourseRepository",
    "FakeEnrolmentRepository",
    "FakeStudentRepository",
    "Store",
    "make_course",
    "make_student",
]


@dataclass
class Store:
    """One shared store, holding every tenant's rows."""

    students: dict[uuid.UUID, Student] = field(default_factory=dict)
    courses: dict[uuid.UUID, Course] = field(default_factory=dict)
    enrolments: dict[uuid.UUID, Enrolment] = field(default_factory=dict)


@dataclass
class AuditRecord:
    action: str
    resource_type: str
    resource_id: str | None
    pii_accessed: bool
    metadata: dict[str, Any]


class FakeAuditSink:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def record(
        self,
        action: str,
        resource_type: str,
        *,
        resource_id: str | None = None,
        pii_accessed: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.records.append(
            AuditRecord(action, resource_type, resource_id, pii_accessed, metadata or {})
        )

    def actions(self) -> list[str]:
        return [record.action for record in self.records]


def _stamp(instance: Any) -> Any:
    """What the INSERT would populate.

    `created_at` and `updated_at` are server defaults, so a freshly constructed
    instance has neither until it reaches the database. Response models require
    both, so the fake flush has to supply them or the tests would be passing
    against a shape production never produces.
    """
    now = datetime.now(UTC)
    if getattr(instance, "created_at", None) is None:
        instance.created_at = now
    instance.updated_at = now
    if getattr(instance, "id", None) is None:
        instance.id = uuid.uuid4()
    return instance


class _Scoped:
    def __init__(self, store: Store, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required to build a repository")
        self.store = store
        self.tenant_id = tenant_id


class FakeStudentRepository(_Scoped):
    def _live(self) -> list[Student]:
        return [
            s
            for s in self.store.students.values()
            if s.tenant_id == self.tenant_id and s.deleted_at is None
        ]

    async def get(self, student_id: uuid.UUID) -> Student | None:
        return next((s for s in self._live() if s.id == student_id), None)

    async def get_many(self, student_ids: list[uuid.UUID]) -> dict[uuid.UUID, Student]:
        wanted = set(student_ids)
        return {s.id: s for s in self._live() if s.id in wanted}

    async def by_number(self, student_number: str) -> Student | None:
        return next((s for s in self._live() if s.student_number == student_number), None)

    async def by_email(self, email_normalized: str) -> Student | None:
        return next((s for s in self._live() if s.email_normalized == email_normalized), None)

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
        query: str | None = None,
    ) -> tuple[list[Student], int]:
        rows = self._live()
        if status:
            rows = [s for s in rows if s.status == status]
        if query:
            needle = query.strip().lower()
            rows = [
                s
                for s in rows
                if needle in s.first_name.lower()
                or needle in s.last_name.lower()
                or needle in s.student_number.lower()
                or needle in s.email_normalized
            ]
        rows.sort(key=lambda s: (s.last_name, s.first_name, str(s.id)))
        return rows[offset : offset + limit], len(rows)

    async def add(self, student: Student) -> Student:
        self._assert_unique(student)
        self.store.students[student.id] = _stamp(student)
        return student

    async def touch(self, student: Student) -> Student:
        self._assert_unique(student)
        self.store.students[student.id] = _stamp(student)
        return student

    def _assert_unique(self, student: Student) -> None:
        # `uq_students_tenant_number` and `uq_students_tenant_email`, both
        # partial on `deleted_at IS NULL`.
        if student.deleted_at is not None:
            return
        for other in self._live():
            if other.id == student.id:
                continue
            if other.student_number == student.student_number:
                raise Conflict("a student with that number already exists")
            if other.email_normalized == student.email_normalized:
                raise Conflict("a student with that email already exists")


class FakeCourseRepository(_Scoped):
    def _rows(self) -> list[Course]:
        return [c for c in self.store.courses.values() if c.tenant_id == self.tenant_id]

    async def get(self, course_id: uuid.UUID) -> Course | None:
        return next((c for c in self._rows() if c.id == course_id), None)

    async def by_code(self, code: str) -> Course | None:
        return next((c for c in self._rows() if c.code == code), None)

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
        query: str | None = None,
        teacher_user_id: str | None = None,
    ) -> tuple[list[Course], int]:
        rows = self._rows()
        if status:
            rows = [c for c in rows if c.status == status]
        if teacher_user_id:
            rows = [c for c in rows if c.teacher_user_id == teacher_user_id]
        if query:
            needle = query.strip().lower()
            rows = [c for c in rows if needle in c.code.lower() or needle in c.title.lower()]
        rows.sort(key=lambda c: (c.code, str(c.id)))
        return rows[offset : offset + limit], len(rows)

    async def add(self, course: Course) -> Course:
        self._assert_unique(course)
        self.store.courses[course.id] = _stamp(course)
        return course

    async def touch(self, course: Course) -> Course:
        self._assert_unique(course)
        self.store.courses[course.id] = _stamp(course)
        return course

    def _assert_unique(self, course: Course) -> None:
        for other in self._rows():
            if other.id != course.id and other.code == course.code:
                raise Conflict("a course with that code already exists")


class FakeEnrolmentRepository(_Scoped):
    def _rows(self) -> list[Enrolment]:
        return [e for e in self.store.enrolments.values() if e.tenant_id == self.tenant_id]

    async def get(self, enrolment_id: uuid.UUID) -> Enrolment | None:
        return next((e for e in self._rows() if e.id == enrolment_id), None)

    async def for_pair(self, student_id: uuid.UUID, course_id: uuid.UUID) -> Enrolment | None:
        return next(
            (e for e in self._rows() if e.student_id == student_id and e.course_id == course_id),
            None,
        )

    async def roster(
        self,
        course_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
    ) -> tuple[list[tuple[Enrolment, Student]], int]:
        pairs: list[tuple[Enrolment, Student]] = []
        for enrolment in self._rows():
            if enrolment.course_id != course_id:
                continue
            if status and enrolment.status != status:
                continue
            student = self.store.students.get(enrolment.student_id)
            # Soft deletion means the ON DELETE CASCADE never fires, so the
            # roster query has to exclude deleted students itself.
            if student is None or student.deleted_at is not None:
                continue
            pairs.append((enrolment, student))
        pairs.sort(key=lambda pair: (pair[1].last_name, pair[1].first_name, str(pair[1].id)))
        return pairs[offset : offset + limit], len(pairs)

    async def for_student(self, student_id: uuid.UUID) -> list[Enrolment]:
        return [e for e in self._rows() if e.student_id == student_id]

    async def add(self, enrolment: Enrolment) -> Enrolment:
        self._assert_unique(enrolment)
        self.store.enrolments[enrolment.id] = _stamp(enrolment)
        return enrolment

    async def touch(self, enrolment: Enrolment) -> Enrolment:
        self._assert_unique(enrolment)
        self.store.enrolments[enrolment.id] = _stamp(enrolment)
        return enrolment

    def _assert_unique(self, enrolment: Enrolment) -> None:
        # `uq_enrolments_student_course`: the constraint that makes a duplicated
        # proposal from the assistant harmless.
        for other in self.store.enrolments.values():
            if (
                other.id != enrolment.id
                and other.student_id == enrolment.student_id
                and other.course_id == enrolment.course_id
            ):
                raise Conflict("that student is already enrolled in that course")


# ── Builders ────────────────────────────────────────────────────────────────


def make_student(
    tenant_id: str,
    *,
    student_number: str = "S001",
    first_name: str = "Ada",
    last_name: str = "Lovelace",
    email: str = "ada@example.com",
    status: str = "active",
    student_id: uuid.UUID | None = None,
) -> Student:
    return _stamp(
        Student(
            id=student_id or uuid.uuid4(),
            tenant_id=tenant_id,
            student_number=student_number,
            first_name=first_name,
            last_name=last_name,
            email=email,
            email_normalized=email.strip().lower(),
            year_level=1,
            status=status,
            student_metadata={},
            created_by="seed",
        )
    )


def make_course(
    tenant_id: str,
    *,
    code: str = "COMP201",
    title: str = "Systems Programming",
    status: str = "active",
    course_id: uuid.UUID | None = None,
    teacher_user_id: str | None = None,
) -> Course:
    return _stamp(
        Course(
            id=course_id or uuid.uuid4(),
            tenant_id=tenant_id,
            code=code,
            title=title,
            description="",
            term="2026-S1",
            teacher_user_id=teacher_user_id,
            credits=15,
            status=status,
        )
    )
