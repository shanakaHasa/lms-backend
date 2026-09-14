"""Enrolment business rules.

This is the service the assistant will eventually drive, so its behaviour is
shaped by what an agent does rather than by what a form does:

* **Enrolling is idempotent.** A retried tool call, a re-delivered queue message
  or an approval clicked twice must all converge on one enrolment. The service
  looks the pair up first and returns what it finds; `UNIQUE (student_id,
  course_id)` is the backstop underneath.
* **Both sides are re-validated against the tenant every time.** At B7 an
  approved proposal executes minutes after it was made, and the student may have
  been deleted in between. Executing on the arguments alone would be a TOCTOU
  bug — so the check happens at execution, not at proposal.
* **A completed enrolment is never silently downgraded.** Re-enrolling someone
  who finished the course returns the completed record untouched, because
  overwriting an outcome is not something a retry should be able to do.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.core.errors import Conflict, NotFound
from app.core.security import Principal
from app.models.enrolment import Enrolment
from app.models.student import Student
from app.repositories.course import CourseRepository
from app.repositories.enrolment import EnrolmentRepository
from app.repositories.student import StudentRepository
from app.services.audit import AuditSink
from app.services.base import assert_same_tenant

__all__ = ["EnrolmentService"]

WRITE = "enrolments:write"
ROSTER_READ = "students:read"


class EnrolmentService:
    def __init__(
        self,
        repo: EnrolmentRepository,
        students: StudentRepository,
        courses: CourseRepository,
        audit: AuditSink,
        principal: Principal,
    ) -> None:
        assert_same_tenant(principal, repo, students, courses)
        self.repo = repo
        self.students = students
        self.courses = courses
        self.audit = audit
        self.principal = principal

    # ── Writes ──────────────────────────────────────────────────────────────

    async def enrol(self, student_id: uuid.UUID, course_id: uuid.UUID) -> tuple[Enrolment, bool]:
        """Returns the enrolment and whether a row was actually created.

        The boolean is what lets the route answer 201 for a new enrolment and
        200 for a repeat, so a client can tell the two apart without the service
        having to raise on the ordinary case of a retry.
        """
        self.principal.require(WRITE)

        # Existence is checked through the tenant-scoped repositories, so a
        # student in another institution is simply not found — the composite
        # foreign key would refuse the row anyway, but a 404 is a far better
        # answer than a constraint violation.
        student = await self.students.get(student_id)
        if student is None:
            raise NotFound("student not found")
        course = await self.courses.get(course_id)
        if course is None:
            raise NotFound("course not found")
        if course.status == "archived":
            raise Conflict(
                "cannot enrol into an archived course", detail={"course_id": str(course_id)}
            )

        existing = await self.repo.for_pair(student_id, course_id)
        if existing is not None:
            return await self._reuse(existing), False

        enrolment = Enrolment(
            id=uuid.uuid4(),
            tenant_id=self.repo.tenant_id,
            student_id=student_id,
            course_id=course_id,
            status="enrolled",
            # Set here rather than left to the column default so the value is
            # known before the flush and behaves identically under the fakes.
            enrolled_at=datetime.now(UTC),
        )
        await self.repo.add(enrolment)
        self.audit.record(
            "enrolment.create",
            "enrolment",
            resource_id=str(enrolment.id),
            pii_accessed=True,
            metadata={"student_id": str(student_id), "course_id": str(course_id)},
        )
        return enrolment, True

    async def withdraw(self, enrolment_id: uuid.UUID) -> None:
        """A withdrawal, not a deletion.

        The row stays so the history survives, and so re-enrolling reuses it —
        which `UNIQUE (student_id, course_id)` requires in any case.
        """
        self.principal.require(WRITE)
        enrolment = await self.repo.get(enrolment_id)
        if enrolment is None:
            raise NotFound("enrolment not found")

        if enrolment.status == "withdrawn":
            return  # Already in the requested state; withdrawing twice is a no-op.

        previous = enrolment.status
        enrolment.status = "withdrawn"
        await self.repo.touch(enrolment)
        self.audit.record(
            "enrolment.withdraw",
            "enrolment",
            resource_id=str(enrolment_id),
            pii_accessed=True,
            metadata={"from": previous},
        )

    # ── Reads ───────────────────────────────────────────────────────────────

    async def roster(
        self,
        course_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
    ) -> tuple[list[tuple[Enrolment, Student]], int]:
        self.principal.require(ROSTER_READ)
        if await self.courses.get(course_id) is None:
            raise NotFound("course not found")

        entries, total = await self.repo.roster(
            course_id, limit=limit, offset=offset, status=status
        )
        self.audit.record(
            "enrolment.roster",
            "course",
            resource_id=str(course_id),
            # A roster is a list of named students, so it is a PII read even
            # though the resource is a course.
            pii_accessed=True,
            metadata={"returned": len(entries), "total": total, "status": status},
        )
        return entries, total

    # ── Internals ───────────────────────────────────────────────────────────

    async def _reuse(self, existing: Enrolment) -> Enrolment:
        if existing.status != "withdrawn":
            # 'enrolled' is already the requested state; 'completed' is an
            # outcome, and a repeated enrol must not overwrite it.
            self.audit.record(
                "enrolment.create.noop",
                "enrolment",
                resource_id=str(existing.id),
                pii_accessed=True,
                metadata={"status": existing.status},
            )
            return existing

        existing.status = "enrolled"
        existing.enrolled_at = datetime.now(UTC)
        await self.repo.touch(existing)
        self.audit.record(
            "enrolment.reactivate",
            "enrolment",
            resource_id=str(existing.id),
            pii_accessed=True,
            metadata={"from": "withdrawn"},
        )
        return existing
