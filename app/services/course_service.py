"""Course business rules.

Courses hold no personal data, so nothing here sets `pii_accessed`. That is the
point of the flag: if every audit row claimed PII access, the partial index over
it would match every row and the access-review query would be a full scan.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.errors import Conflict, NotFound
from app.core.security import Principal
from app.models.course import Course
from app.repositories.course import CourseRepository
from app.schemas.course import CourseCreate, CourseUpdate, normalise_code
from app.services.audit import AuditSink
from app.services.base import assert_same_tenant

__all__ = ["CourseService"]

READ = "courses:read"
WRITE = "courses:write"


class CourseService:
    def __init__(self, repo: CourseRepository, audit: AuditSink, principal: Principal) -> None:
        assert_same_tenant(principal, repo)
        self.repo = repo
        self.audit = audit
        self.principal = principal

    # ── Reads ───────────────────────────────────────────────────────────────

    async def get(self, course_id: uuid.UUID) -> Course:
        self.principal.require(READ)
        course = await self.repo.get(course_id)
        if course is None:
            raise NotFound("course not found")
        self.audit.record("course.read", "course", resource_id=str(course_id))
        return course

    async def get_by_code(self, code: str) -> Course:
        """Resolve a course the way a person names it.

        The assistant's tools call this: a teacher asks about "COMP201", not
        about a UUID. Normalising here means `comp201` and `COMP201 ` resolve to
        the same course, which is the difference between the agent answering and
        the agent apologising.
        """
        self.principal.require(READ)
        course = await self.repo.by_code(normalise_code(code))
        if course is None:
            raise NotFound(f"no course with code {code!r}")
        self.audit.record("course.read", "course", resource_id=str(course.id))
        return course

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
        query: str | None = None,
        teacher_user_id: str | None = None,
    ) -> tuple[list[Course], int]:
        self.principal.require(READ)
        courses, total = await self.repo.list(
            limit=limit,
            offset=offset,
            status=status,
            query=query,
            teacher_user_id=teacher_user_id,
        )
        self.audit.record(
            "course.list",
            "course",
            metadata={"returned": len(courses), "total": total},
        )
        return courses, total

    # ── Writes ──────────────────────────────────────────────────────────────

    async def create(self, payload: CourseCreate) -> Course:
        self.principal.require(WRITE)

        code = normalise_code(payload.code)
        if await self.repo.by_code(code) is not None:
            raise Conflict(f"a course with code {code!r} already exists", detail={"field": "code"})

        course = Course(
            id=uuid.uuid4(),
            tenant_id=self.repo.tenant_id,
            code=code,
            title=payload.title,
            description=payload.description,
            term=payload.term,
            teacher_user_id=payload.teacher_user_id,
            credits=payload.credits,
            status=payload.status,
        )
        await self.repo.add(course)
        self.audit.record(
            "course.create", "course", resource_id=str(course.id), metadata={"code": code}
        )
        return course

    async def update(self, course_id: uuid.UUID, payload: CourseUpdate) -> Course:
        self.principal.require(WRITE)
        course = await self.repo.get(course_id)
        if course is None:
            raise NotFound("course not found")

        changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
        if not changes:
            raise Conflict("no fields to update")

        # `code` is absent from CourseUpdate by design — renaming it would
        # strand every material already ingested against the old code.
        for field in ("title", "description", "status"):
            if field in changes and changes[field] is not None:
                setattr(course, field, changes[field])

        # Nullable fields, so an explicit null clears them.
        for field in ("term", "teacher_user_id", "credits"):
            if field in changes:
                setattr(course, field, changes[field])

        await self.repo.touch(course)
        self.audit.record(
            "course.update",
            "course",
            resource_id=str(course_id),
            metadata={"changed": sorted(changes)},
        )
        return course
