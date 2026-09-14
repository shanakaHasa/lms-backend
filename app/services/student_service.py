"""Student business rules.

The scope check lives **here**, not in the route decorator. At step B6 the
assistant's tools call this service directly, and at B7 an approved proposal is
executed by this service under the approver's principal — neither path goes
through FastAPI. A gate that only exists in the HTTP layer would be absent from
exactly the two callers that most need it.

Nothing in this module takes a tenant argument. The repository is already scoped
to one, so "which institution" is not a decision this layer can get wrong.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.errors import Conflict, NotFound
from app.core.security import Principal
from app.models.student import Student
from app.repositories.student import StudentRepository
from app.schemas.student import (
    StudentCreate,
    StudentUpdate,
    normalise_email,
    normalise_student_number,
)
from app.services.audit import AuditSink
from app.services.base import assert_same_tenant

__all__ = ["StudentService"]

READ = "students:read"
WRITE = "students:write"


class StudentService:
    def __init__(self, repo: StudentRepository, audit: AuditSink, principal: Principal) -> None:
        assert_same_tenant(principal, repo)
        self.repo = repo
        self.audit = audit
        self.principal = principal

    # ── Reads ───────────────────────────────────────────────────────────────

    async def get(self, student_id: uuid.UUID) -> Student:
        self.principal.require(READ)
        student = await self.repo.get(student_id)
        if student is None:
            # A student in another tenant reaches this line as `None`, so a
            # cross-tenant probe gets exactly the response a genuinely missing
            # id gets. A 403 here would confirm the record exists.
            raise NotFound("student not found")
        self.audit.record("student.read", "student", resource_id=str(student_id), pii_accessed=True)
        return student

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
        query: str | None = None,
    ) -> tuple[list[Student], int]:
        self.principal.require(READ)
        students, total = await self.repo.list(
            limit=limit, offset=offset, status=status, query=query
        )
        # One row for the whole page, carrying the count. Not one per student:
        # audit volume should follow intent, and fifty rows for one page would
        # bury the single lookup an investigation is actually after.
        self.audit.record(
            "student.list",
            "student",
            pii_accessed=True,
            metadata={
                "returned": len(students),
                "total": total,
                "limit": limit,
                "offset": offset,
                "status": status,
                # Recorded in full: "who searched for this person's name" is a
                # question access reviews genuinely ask, and the answer is
                # useless without the term.
                "query": query,
            },
        )
        return students, total

    # ── Writes ──────────────────────────────────────────────────────────────

    async def create(self, payload: StudentCreate) -> Student:
        self.principal.require(WRITE)

        student_number = normalise_student_number(payload.student_number)
        email_normalized = normalise_email(payload.email)
        await self._assert_available(student_number, email_normalized, excluding=None)

        student = Student(
            # Generated here rather than by the database, so the audit row can
            # carry the id in the same transaction with no round trip.
            id=uuid.uuid4(),
            tenant_id=self.repo.tenant_id,
            student_number=student_number,
            first_name=payload.first_name,
            last_name=payload.last_name,
            # `email` keeps what was typed; `email_normalized` is what the
            # unique index and every lookup use.
            email=payload.email.strip(),
            email_normalized=email_normalized,
            year_level=payload.year_level,
            status=payload.status,
            student_metadata=payload.metadata,
            # From the token. A caller-supplied value here would make the trail
            # forgeable, which is why it is absent from the request schema.
            created_by=self.principal.user_id,
        )
        await self.repo.add(student)
        self.audit.record(
            "student.create",
            "student",
            resource_id=str(student.id),
            pii_accessed=True,
            metadata={"student_number": student_number},
        )
        return student

    async def update(self, student_id: uuid.UUID, payload: StudentUpdate) -> Student:
        self.principal.require(WRITE)
        student = await self.repo.get(student_id)
        if student is None:
            raise NotFound("student not found")

        # `exclude_unset` is what separates "field omitted" from "field set to
        # null". Without it every PATCH would blank every field the caller did
        # not mention.
        changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
        if not changes:
            raise Conflict("no fields to update")

        if "email" in changes and changes["email"] is not None:
            email_normalized = normalise_email(changes["email"])
            if email_normalized != student.email_normalized:
                await self._assert_available(None, email_normalized, excluding=student_id)
            student.email = changes["email"].strip()
            student.email_normalized = email_normalized

        for field in ("first_name", "last_name", "status"):
            if field in changes and changes[field] is not None:
                setattr(student, field, changes[field])

        # The one field where an explicit null means "clear it": year_level is
        # nullable in the schema, so a caller has to be able to unset it.
        if "year_level" in changes:
            student.year_level = changes["year_level"]

        if "metadata" in changes and changes["metadata"] is not None:
            student.student_metadata = changes["metadata"]

        await self.repo.touch(student)
        self.audit.record(
            "student.update",
            "student",
            resource_id=str(student_id),
            pii_accessed=True,
            # Field names, not values. The audit log records that the email
            # changed; copying the old and new addresses into it would spread
            # the same personal data across a second table with a different
            # retention policy.
            metadata={"changed": sorted(changes)},
        )
        return student

    async def delete(self, student_id: uuid.UUID) -> None:
        self.principal.require(WRITE)
        student = await self.repo.get(student_id)
        if student is None:
            raise NotFound("student not found")

        # Soft, so enrolments, grades and audit rows still resolve to a person.
        # The partial unique indexes exclude deleted rows, which is what frees
        # the student number and the address for reuse.
        student.deleted_at = datetime.now(UTC)
        await self.repo.touch(student)
        self.audit.record(
            "student.delete",
            "student",
            resource_id=str(student_id),
            pii_accessed=True,
            metadata={"soft": True},
        )

    # ── Internals ───────────────────────────────────────────────────────────

    async def _assert_available(
        self,
        student_number: str | None,
        email_normalized: str | None,
        *,
        excluding: uuid.UUID | None,
    ) -> None:
        """Check the identifiers before inserting.

        The unique indexes are the real guarantee; this exists so the ordinary
        case — a teacher re-submitting a form — gets a precise 409 naming the
        field, rather than a generic constraint message.
        """
        if student_number is not None:
            existing = await self.repo.by_number(student_number)
            if existing is not None and existing.id != excluding:
                raise Conflict(
                    "a student with that number already exists",
                    detail={"field": "student_number"},
                )
        if email_normalized is not None:
            existing = await self.repo.by_email(email_normalized)
            if existing is not None and existing.id != excluding:
                raise Conflict(
                    "a student with that email already exists", detail={"field": "email"}
                )
